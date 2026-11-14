from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ApiConfig:
    base_url: str = "http://localhost:3000/api/v1"
    timeout_s: float = 5.0
    get_retries: int = 2            
    verify_tls: bool = True
    time_format: str = "iso"      

    @classmethod
    def from_env(cls) -> "ApiConfig":
        return cls(
            base_url=os.getenv("TRANSIT_API_URL", cls.base_url),
            timeout_s=float(os.getenv("TRANSIT_API_TIMEOUT_S", cls.timeout_s)),
            get_retries=int(os.getenv("TRANSIT_API_GET_RETRIES", cls.get_retries)),
            verify_tls=os.getenv("TRANSIT_API_VERIFY_TLS", "1") not in ("0", "false", "False"),
            time_format=os.getenv("TRANSIT_API_TIME_FORMAT", cls.time_format),
        )