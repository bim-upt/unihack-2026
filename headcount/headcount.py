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


def count_people(image, model_path: str = MODEL_PATH, conf: float = CONF) -> int:
    frame = _to_bgr(image)
    result = _get_model(model_path).predict(
        frame, conf=conf, imgsz=IMGSZ, classes=PERSON_CLASSES, verbose=False
    )[0]
    return len(result.boxes)


def load_image_for_counter(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


if __name__ == "__main__":
    import sys

    warmup()
    if len(sys.argv) > 1:
        print(count_people(load_image_for_counter(sys.argv[1])))