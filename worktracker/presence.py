"""Камераар хүн (царай) байгаа эсэхийг шалгана.

Зураг, бичлэг ХАДГАЛАХГҮЙ: кадр зөвхөн санах ойд шалгагдаад шууд устана.
"""
from __future__ import annotations

import logging
import math
import os
import random
import sys
import threading
import time

os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")
import cv2  # noqa: E402

from .config import resource_path  # noqa: E402

log = logging.getLogger(__name__)

YUNET_MODEL = "face_detection_yunet_2023mar.onnx"
SPOOF_MODEL = "face_antispoof_minifasnetv2.onnx"
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

    def detect_faces(self, frame) -> list[tuple[tuple[int, int, int, int], tuple | None]]:
        """Return face boxes and YuNet landmarks in original frame coordinates."""
        h, w = frame.shape[:2]
        scale = DETECT_WIDTH / float(w)
        small = cv2.resize(frame, (DETECT_WIDTH, int(h * scale)))
        found: list = []
        if self._yunet is not None:
            self._yunet.setInputSize((small.shape[1], small.shape[0]))
            _, faces = self._yunet.detect(small)
            if faces is not None:
                for f in faces:
                    box = tuple(int(v / scale) for v in f[:4])
                    points = tuple((float(f[i] / scale), float(f[i + 1] / scale))
                                   for i in range(4, 14, 2))
                    found.append((box, points))
        else:
            gray = cv2.equalizeHist(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY))
            boxes = self._haar.detectMultiScale(gray, 1.1, 5, minSize=(30, 30))
            found = [(tuple(int(v / scale) for v in b), None) for b in boxes]
        return found

    def detect(self, frame) -> list[tuple[int, int, int, int]]:
        """Return face boxes in original frame coordinates."""
        return [box for box, _ in self.detect_faces(frame)]


class FaceSpoofDetector:
    """MiniFASNet classifier for real faces versus photo/screen presentation attacks."""

    def __init__(self):
        model = resource_path("assets", SPOOF_MODEL)
        self.net = None
        self.unavailable = False
        self.spoof_streak = 0
        try:
            self.net = cv2.dnn.readNetFromONNX(str(model))
        except Exception:  # noqa: BLE001
            self.unavailable = True
            log.exception("Зураг/видео хуурмаг илрүүлэх загвар ачаалж чадсангүй")

    def reset(self) -> None:
        self.spoof_streak = 0

    def is_spoof(self, frame, box) -> bool:
        if self.unavailable:
            return True
        image_h, image_w = frame.shape[:2]
        x, y, box_w, box_h = box
        if box_w <= 0 or box_h <= 0:
            return False
        scale = min((image_h - 1) / box_h, (image_w - 1) / box_w, 2.7)
        crop_w, crop_h = box_w * scale, box_h * scale
        center_x, center_y = x + box_w / 2, y + box_h / 2
        x1 = max(0, int(center_x - crop_w / 2))
        y1 = max(0, int(center_y - crop_h / 2))
        x2 = min(image_w - 1, int(center_x + crop_w / 2))
        y2 = min(image_h - 1, int(center_y + crop_h / 2))
        crop = frame[y1:y2 + 1, x1:x2 + 1]
        if crop.size == 0:
            return False

        crop = cv2.resize(crop, (80, 80))
        blob = cv2.dnn.blobFromImage(crop, scalefactor=1.0, size=(80, 80), swapRB=False)
        try:
            self.net.setInput(blob)
            logits = self.net.forward().reshape(-1)
            if len(logits) < 2:
                self.unavailable = True
                return True
            maximum = float(max(logits))
            exp_scores = [math.exp(float(score) - maximum) for score in logits]
            real_probability = exp_scores[1] / sum(exp_scores)
            self.spoof_streak = self.spoof_streak + 1 if real_probability < 0.5 else 0
            return self.spoof_streak >= 3
        except Exception:  # noqa: BLE001
            self.unavailable = True
            log.exception("Зураг/видео хуурмаг илрүүлэхэд алдаа гарлаа")
            return True


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
        self._challenge_active = False
        self._challenge_failed = False
        self._challenge_started = 0.0
        self._challenge_baseline = 0.0
        self._challenge_step = 0
        self._challenge_first_direction = 1
        self._next_challenge_at = time.monotonic() + random.uniform(30, 60)
        self.reset()

    def reset(self) -> None:
        self.ref_box = None
        self.prev = None
        self.last_motion = time.monotonic()

    def defer_challenge(self) -> None:
        self._challenge_active = False
        self._next_challenge_at = time.monotonic() + random.uniform(30, 60)

    @property
    def challenge_failed(self) -> bool:
        return self._challenge_failed

    @property
    def challenge_prompt(self) -> str:
        if self._challenge_failed and not self._challenge_active:
            return "Амьд эсэх шалгалт амжилтгүй — камер руу харж дахин оролдоно уу"
        if not self._challenge_active:
            return ""
        direction = self._challenge_first_direction * (1 if self._challenge_step == 0 else -1)
        arrow = "→" if direction > 0 else "←"
        return f"Нүүрээ камерын кадрын {arrow} чигт эргүүл ({self._challenge_step + 1}/2)"

    @staticmethod
    def _yaw(points) -> float | None:
        if not points or len(points) < 3:
            return None
        eye_a, eye_b, nose = points[:3]
        axis_x, axis_y = eye_b[0] - eye_a[0], eye_b[1] - eye_a[1]
        eye_distance = math.hypot(axis_x, axis_y)
        if eye_distance < 1:
            return None
        midpoint_x = (eye_a[0] + eye_b[0]) / 2
        midpoint_y = (eye_a[1] + eye_b[1]) / 2
        return ((nose[0] - midpoint_x) * axis_x + (nose[1] - midpoint_y) * axis_y) / eye_distance**2

    def update_challenge(self, points, now: float) -> None:
        """Ask for two unpredictable head turns and verify YuNet face geometry."""
        yaw = self._yaw(points)
        if self._challenge_active:
            if now - self._challenge_started > 25:
                self._challenge_active = False
                self._challenge_failed = True
                self._next_challenge_at = now + 30
                return
            if yaw is None:
                return
            delta = yaw - self._challenge_baseline
            direction = self._challenge_first_direction * (1 if self._challenge_step == 0 else -1)
            if direction * delta >= 0.16:
                self._challenge_step += 1
                if self._challenge_step >= 2:
                    self._challenge_active = False
                    self._challenge_failed = False
                    self._next_challenge_at = now + random.uniform(20 * 60, 40 * 60)
            return

        if now >= self._next_challenge_at and yaw is not None:
            self._challenge_active = True
            self._challenge_started = now
            self._challenge_baseline = yaw
            self._challenge_step = 0
            self._challenge_first_direction = random.choice((-1, 1))

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
        self.spoof_detector = FaceSpoofDetector()
        self.liveness = Liveness()
        self.camera_ok = False
        self.last_face_time = 0.0  # амьд царай харагдсан сүүлийн цаг (time.monotonic())
        self.face_static = False   # царай байгаа ч хөдөлгөөнгүй (зураг байж магадгүй)
        self.blocked = False       # камер таглагдсан / хар
        self._dark_since: float | None = None
        self._cap = None
        self._stop = threading.Event()
        self._paused = threading.Event()   # "Зогсоох" дарсан → камерыг хааж, гэрлийг унтраана
        self._reopen_now = False           # "Үргэлжлүүлэх" дарсан → 10 сек хүлээлгүй шууд нээнэ
        self._thread: threading.Thread | None = None

    def set_paused(self, paused: bool) -> None:
        """Зогсоосон үед камер огт ажиллахгүй. Камерын thread ≤ 1 сек-д хааж / дахин нээнэ."""
        if paused:
            self._paused.set()
        else:
            self.liveness.defer_challenge()
            self._reopen_now = True
            self._paused.clear()

    @property
    def camera_state(self) -> str:
        if not self.camera_ok:
            return "none"
        if self.blocked:
            return "blocked"
        if self.face_static:
            return "static"
        return "ok"

    @property
    def challenge_prompt(self) -> str:
        return self.liveness.challenge_prompt if self.cfg.get("liveness_enabled", True) else ""

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
        faces = [] if self.blocked else self.detector.detect_faces(frame)
        if not faces:
            self.liveness.update_challenge(None, now)
            self.liveness.reset()
            self.spoof_detector.reset()
            self.face_static = self.liveness.challenge_failed
            return
        if self.cfg.get("liveness_enabled", True):
            box, points = max(faces, key=lambda item: item[0][2] * item[0][3])
            self.liveness.update_challenge(points, now)
            self.liveness.update(gray, box, now, float(self.cfg.get("liveness_threshold", MOTION_THRESHOLD)))
            spoofed = self.spoof_detector.is_spoof(frame, box)
            static = spoofed or self.liveness.challenge_failed or self.liveness.is_static(
                now, float(self.cfg.get("liveness_static_sec", 120)))
            if static and not self.face_static:
                log.info("Амьд царай баталгаажаагүй (зураг/бичлэг байж магадгүй)")
            self.face_static = static
        else:
            self.liveness.defer_challenge()
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
            if self._paused.is_set():
                if self._cap is not None:
                    self._cap.release()  # камерын гэрэл унтарна
                    self._cap = None
                    log.info("Зогсоосон: камерыг хаалаа")
                self.camera_ok = False
                self.blocked = self.face_static = False
                self._dark_since = None
                self.liveness.reset()
                self._stop.wait(0.3)
                continue
            if self._cap is None:
                if not self._reopen_now and self._stop.wait(10):
                    break
                if self._paused.is_set():
                    continue
                reopened, self._reopen_now = self._reopen_now, False
                self._cap = _open_camera(self.camera_index)
                self.camera_ok = self._cap is not None
                if self.camera_ok:
                    log.info("Камер дахин нээгдлээ" if reopened else "Камер дахин холбогдлоо")
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
