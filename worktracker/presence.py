"""Камераар хүн (царай) байгаа эсэхийг шалгана.

Зураг, бичлэг ХАДГАЛАХГҮЙ: кадр зөвхөн санах ойд шалгагдаад шууд устана.
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import time

os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")
import cv2  # noqa: E402

from .config import resource_path  # noqa: E402

log = logging.getLogger(__name__)

YUNET_MODEL = "face_detection_yunet_2023mar.onnx"
DETECT_WIDTH = 320  # хурдан ажиллуулахын тулд кадрыг жижигрүүлнэ


class FaceDetector:
    """YuNet (OpenCV DNN) загвараар царай илрүүлнэ. Байхгүй бол Haar cascade."""

    def __init__(self, score_threshold: float = 0.6):
        self._yunet = None
        self._haar = None
        model = resource_path("assets", YUNET_MODEL)
        if model.is_file() and hasattr(cv2, "FaceDetectorYN"):
            self._yunet = cv2.FaceDetectorYN.create(
                str(model), "", (DETECT_WIDTH, 240), score_threshold, 0.3, 20
            )
            log.info("Царай илрүүлэгч: YuNet")
        elif hasattr(cv2, "CascadeClassifier"):
            path = os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml")
            self._haar = cv2.CascadeClassifier(path)
            log.info("Царай илрүүлэгч: Haar cascade")
        else:
            raise RuntimeError(f"Царай илрүүлэх загвар олдсонгүй: {model}")

    def detect(self, frame) -> list[tuple[int, int, int, int]]:
        """Кадр дээрх царайнуудын (x, y, w, h) жагсаалт (эх кадрын хэмжээгээр)."""
        h, w = frame.shape[:2]
        scale = DETECT_WIDTH / float(w)
        small = cv2.resize(frame, (DETECT_WIDTH, int(h * scale)))
        boxes: list = []
        if self._yunet is not None:
            self._yunet.setInputSize((small.shape[1], small.shape[0]))
            _, faces = self._yunet.detect(small)
            if faces is not None:
                boxes = [tuple(f[:4]) for f in faces]
        else:
            gray = cv2.equalizeHist(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY))
            boxes = list(self._haar.detectMultiScale(gray, 1.1, 5, minSize=(30, 30)))
        return [tuple(int(v / scale) for v in b) for b in boxes]


def _open_camera(index: int):
    if sys.platform == "darwin":
        backend = cv2.CAP_AVFOUNDATION
    elif sys.platform == "win32":
        backend = cv2.CAP_DSHOW
    else:
        backend = cv2.CAP_ANY
    cap = cv2.VideoCapture(index, backend)
    if not cap.isOpened():
        cap.release()
        return None
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return cap


class PresenceMonitor:
    """Арын thread дээр камераас кадр уншиж, царай харагдсан сүүлийн цагийг хадгална."""

    def __init__(self, camera_index: int = 0, interval: float = 1.0):
        self.camera_index = camera_index
        self.interval = max(0.2, float(interval))
        self.detector = FaceDetector()
        self.camera_ok = False
        self.last_face_time = 0.0  # time.monotonic()
        self._cap = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        # macOS дээр камерын зөвшөөрлийн цонх main thread-ээс гарах ёстой тул
        # анхны нээлтийг энд (main thread) хийнэ.
        self._cap = _open_camera(self.camera_index)
        self.camera_ok = self._cap is not None
        if not self.camera_ok:
            log.warning("Камер нээгдсэнгүй (index=%s). Гар/хулганаар л шалгана.", self.camera_index)
        self._thread = threading.Thread(target=self._run, name="camera", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    def _run(self) -> None:
        failures = 0
        while not self._stop.is_set():
            if self._cap is None:
                if self._stop.wait(10):
                    break
                self._cap = _open_camera(self.camera_index)
                self.camera_ok = self._cap is not None
                if self.camera_ok:
                    log.info("Камер дахин холбогдлоо")
                continue

            self._cap.grab()  # буферт хуучирсан кадрыг алгасна
            ok, frame = self._cap.read()
            if not ok or frame is None:
                failures += 1
                if failures >= 5:
                    log.warning("Камераас кадр уншиж чадсангүй, дахин холбогдоно")
                    self._cap.release()
                    self._cap = None
                    self.camera_ok = False
                    failures = 0
                self._stop.wait(1)
                continue

            failures = 0
            self.camera_ok = True
            try:
                if self.detector.detect(frame):
                    self.last_face_time = time.monotonic()
            except Exception:  # noqa: BLE001
                log.exception("Царай илрүүлэхэд алдаа гарлаа")
            del frame  # кадрыг хадгалахгүй
            self._stop.wait(self.interval)


def camera_test(camera_index: int = 0) -> None:
    """Камер, царай илрүүлэлтийг шалгах цонх (юу ч хадгалахгүй). Q эсвэл Esc дарж гарна."""
    detector = FaceDetector()
    cap = _open_camera(camera_index)
    if cap is None:
        print(f"Камер нээгдсэнгүй (index={camera_index})")
        return
    print("Камерын тест: Q эсвэл Esc дарж гарна.")
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            faces = detector.detect(frame)
            for x, y, w, h in faces:
                cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 200, 0), 2)
            label = "Hun baina" if faces else "Hun alga"
            color = (0, 200, 0) if faces else (0, 0, 220)
            cv2.putText(frame, label, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.9, color, 2)
            cv2.imshow("Camera test", frame)
            if cv2.waitKey(30) & 0xFF in (ord("q"), 27):
                break
    finally:
        cap.release()
        cv2.destroyAllWindows()
