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


# Хөдөлгөөнгүй зургийг амьд хүнээс ялгах босго (normalized 32×32 нүүрний ялгаа).
# Хэмжилт: зураг + камерын шуугиан ≤ 0.035 (бүдэг гэрэлд), 0.5px толгойн хөдөлгөөн ≈ 0.06+.
MOTION_THRESHOLD = 0.05
BIG_SHIFT = 0.15          # бокс өргөний 15%-аас их шилжвэл том хөдөлгөөн
BLOCK_MEAN, BLOCK_STD, BLOCK_UNIFORM_STD = 25.0, 12.0, 5.0


class Liveness:
    """Камерт харагдаж буй царай хөдөлж байна уу (амьд хүн), эсвэл огт хөдлөхгүй байна уу (зураг).

    Амьд хүн нүдээ ирмэх, амьсгалах, бага зэрэг хөдлөхөд нүүрний хэсэг кадр хооронд өөрчлөгдөнө.
    Хэвлэсэн зураг тавьсан үед зөвхөн камерын шуугиан л үлдэнэ.
    """

    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self.ref_box = None
        self.prev = None
        self.last_motion = time.monotonic()

    @staticmethod
    def _crop(gray, box):
        import numpy as np

        x, y, w, h = box
        H, W = gray.shape[:2]
        x0, y0, x1, y1 = max(0, x), max(0, y), min(W, x + w), min(H, y + h)
        if x1 - x0 < 8 or y1 - y0 < 8:
            return None
        c = cv2.resize(gray[y0:y1, x0:x1], (32, 32), interpolation=cv2.INTER_AREA).astype(np.float32)
        return (c - c.mean()) / (c.std() + 1.0)  # гэрлийн ерөнхий өөрчлөлтийг арилгана

    def update(self, gray, box, now: float, threshold: float = MOTION_THRESHOLD) -> float:
        import numpy as np

        if self.ref_box is not None:
            rx, ry, rw, rh = self.ref_box
            shift = np.hypot((box[0] + box[2] / 2) - (rx + rw / 2), (box[1] + box[3] / 2) - (ry + rh / 2)) / max(1, rw)
            if shift > BIG_SHIFT:
                self.ref_box, self.prev, self.last_motion = box, None, now
                return 1.0
        else:
            self.ref_box = box
        cur = self._crop(gray, self.ref_box)
        if cur is None:
            return 0.0
        diff = 0.0 if self.prev is None else float(np.mean(np.abs(cur - self.prev)))
        if self.prev is None or diff > threshold:
            self.last_motion = now
            self.ref_box = box  # шинэ байрлалд дахин бэхэлнэ
            cur = self._crop(gray, box)
        self.prev = cur
        return diff

    def is_static(self, now: float, static_after_sec: float = 120.0) -> bool:
        return self.ref_box is not None and now - self.last_motion > static_after_sec


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

    def __init__(self, camera_index: int = 0, interval: float = 1.0, cfg: dict | None = None):
        self.camera_index = camera_index
        self.interval = max(0.2, float(interval))
        self.cfg = cfg if cfg is not None else {}
        self.detector = FaceDetector()
        self.liveness = Liveness()
        self.camera_ok = False
        self.last_face_time = 0.0  # амьд царай харагдсан сүүлийн цаг (time.monotonic())
        self.face_static = False   # царай байгаа ч хөдөлгөөнгүй (зураг байж магадгүй)
        self.blocked = False       # камер таглагдсан / хар
        self._dark_since: float | None = None
        self._cap = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def camera_state(self) -> str:
        if not self.camera_ok:
            return "none"
        if self.blocked:
            return "blocked"
        if self.face_static:
            return "static"
        return "ok"

    def _check_blocked(self, gray, now: float) -> None:
        """Кадр бараг хар эсвэл нэг өнгөтэй (наалт, хуруу) удаан үргэлжилбэл → таглагдсан."""
        small = cv2.resize(gray, (80, 60), interpolation=cv2.INTER_AREA)
        mean, std = float(small.mean()), float(small.std())
        dark = (mean < BLOCK_MEAN and std < BLOCK_STD) or std < BLOCK_UNIFORM_STD
        if not dark:
            self._dark_since = None
            self.blocked = False
        elif self._dark_since is None:
            self._dark_since = now
        elif now - self._dark_since >= float(self.cfg.get("camera_blocked_sec", 30)):
            if not self.blocked:
                log.info("Камер таглагдсан бололтой (mean=%.0f std=%.0f)", mean, std)
            self.blocked = True

    def _process(self, frame) -> None:
        now = time.monotonic()
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        self._check_blocked(gray, now)
        faces = [] if self.blocked else self.detector.detect(frame)
        if not faces:
            self.liveness.reset()
            self.face_static = False
            return
        if self.cfg.get("liveness_enabled", True):
            box = max(faces, key=lambda b: b[2] * b[3])
            self.liveness.update(gray, box, now, float(self.cfg.get("liveness_threshold", MOTION_THRESHOLD)))
            static = self.liveness.is_static(now, float(self.cfg.get("liveness_static_sec", 120)))
            if static and not self.face_static:
                log.info("Хөдөлгөөнгүй царай илэрлээ (зураг байж магадгүй)")
            self.face_static = static
        else:
            self.face_static = False
        if not self.face_static:
            self.last_face_time = now

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
                self._process(frame)
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
