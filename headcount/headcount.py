import io
from functools import lru_cache

import cv2
import numpy as np
from PIL import Image
from ultralytics import YOLO

MODEL_PATH = "yolov8m.pt"
PERSON_CLASSES = [0]
CONF = 0.2
IMGSZ = 1280


@lru_cache(maxsize=1)
def _get_model(path: str = MODEL_PATH) -> YOLO:
    return YOLO(path)


def warmup(model_path: str = MODEL_PATH, runs: int = 2) -> None:
    model = _get_model(model_path)
    dummy = np.zeros((IMGSZ, IMGSZ, 3), dtype=np.uint8)
    for _ in range(runs):
        model.predict(
            dummy, conf=CONF, imgsz=IMGSZ, classes=PERSON_CLASSES, verbose=False
        )



def _to_bgr(image) -> np.ndarray:
    if isinstance(image, (bytes, bytearray)):
        arr = cv2.imdecode(np.frombuffer(image, np.uint8), cv2.IMREAD_COLOR)
        if arr is None:
            raise ValueError("Could not decode image bytes")
        return arr
    if isinstance(image, Image.Image):
        return cv2.cvtColor(np.array(image.convert("RGB")), cv2.COLOR_RGB2BGR)
    if isinstance(image, np.ndarray):
        return image
    raise TypeError(f"Unsupported image type: {type(image)}")


# the boxes drawn on the picture: thin and see-through, so the people inside stay easy to look at
BOX_COLOR = (0, 255, 0)  # BGR: green
BOX_ALPHA = 0.45         # 0 = invisible, 1 = solid
JPEG_QUALITY = 85


def detect_people(image, model_path: str = MODEL_PATH, conf: float = CONF):
    """The photo (BGR) and the boxes of the people found in it: an (n, 4) array of x1, y1, x2, y2."""
    frame = _to_bgr(image)
    result = _get_model(model_path).predict(
        frame, conf=conf, imgsz=IMGSZ, classes=PERSON_CLASSES, verbose=False
    )[0]
    return frame, result.boxes.xyxy.cpu().numpy()


def count_people(image, model_path: str = MODEL_PATH, conf: float = CONF) -> int:
    return len(detect_people(image, model_path, conf)[1])


def draw_boxes(frame: np.ndarray, boxes: np.ndarray) -> np.ndarray:
    """A copy of the photo with a thin, see-through box around each person (1 px on a photo up to ~1000 px wide,
    a little thicker on bigger ones, so it stays visible but never hides the person)."""
    overlay = frame.copy()
    thickness = max(1, round(max(frame.shape[:2]) / 1000))
    for x1, y1, x2, y2 in boxes.astype(int):
        cv2.rectangle(overlay, (x1, y1), (x2, y2), BOX_COLOR, thickness, lineType=cv2.LINE_AA)
    return cv2.addWeighted(overlay, BOX_ALPHA, frame, 1 - BOX_ALPHA, 0)


def to_jpeg(frame: np.ndarray, quality: int = JPEG_QUALITY) -> bytes:
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise ValueError("Could not encode the picture")
    return buf.tobytes()


def count_and_draw(image, model_path: str = MODEL_PATH, conf: float = CONF) -> tuple[int, bytes]:
    """The number of people and the photo with their boxes drawn on it (JPEG bytes)."""
    frame, boxes = detect_people(image, model_path, conf)
    return len(boxes), to_jpeg(draw_boxes(frame, boxes))


def load_image_for_counter(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()

#--- server

import base64
import threading

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.concurrency import run_in_threadpool

HOST = "127.0.0.1"
PORT = 8000

_lock = threading.Lock()

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _count_locked(data: bytes, with_image: bool):
    with _lock:
        if with_image:
            return count_and_draw(data)
        return count_people(data), None


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/count")
async def count(request: Request):
    if request.headers.get("content-type", "").startswith("multipart/form-data"):
        form = await request.form()
        upload = form.get("file")
        if upload is None:
            raise HTTPException(400, "Missing 'file' field")
        data = await upload.read()
    else:
        data = await request.body()

    if not data:
        raise HTTPException(400, "Empty request body")

    # the picture with the boxes comes along (base64 JPEG) unless asked not to: POST /count?image=0
    with_image = request.query_params.get("image", "1").lower() not in ("0", "false", "no")
    try:
        n, picture = await run_in_threadpool(_count_locked, data, with_image)
    except ValueError as e:
        raise HTTPException(400, str(e))
    answer = {"count": n}
    if picture is not None:
        answer["image"] = base64.b64encode(picture).decode("ascii")
        answer["image_type"] = "image/jpeg"
    return answer


if __name__ == "__main__":
    import sys

    import uvicorn

    warmup()
    if len(sys.argv) > 1:
        # python headcount.py photo.jpg -> prints the count and saves the photo with the boxes next to it
        import os

        n, picture = count_and_draw(load_image_for_counter(sys.argv[1]))
        root, _ = os.path.splitext(sys.argv[1])
        with open(f"{root}_boxes.jpg", "wb") as f:
            f.write(picture)
        print(n)
    else:
        uvicorn.run(app, host=HOST, port=PORT)