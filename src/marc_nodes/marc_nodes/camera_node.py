#!/usr/bin/env python3
"""
camera_node.py — Caméra + détection ArUco de MARC en nœud ROS2.

Porté depuis vision/camera_service.py. La caméra est ouverte UNE fois.
Deux sorties, deux publics :

  ROS2 (vers localization_node / navigation_node)
    /aruco/detections   std_msgs/String   (JSON {id: {distance, angle}})

  HTTP MJPEG (vers l'interface web — INCHANGÉ par rapport à camera_service.py)
    GET http://<pi>:5001/video        flux MJPEG annoté
    GET http://<pi>:5001/detections   JSON (compat web)
    GET http://<pi>:5001/health

Pourquoi garder /video en HTTP : un navigateur ne sait pas lire un topic
ROS2 (DDS). Le flux MJPEG reste donc servi en HTTP pour que app.js continue
d'afficher la caméra sans modification.
"""

import os
import time
import json
import math
import threading

import cv2
import cv2.aruco as aruco
import numpy as np
from flask import Flask, Response, jsonify

import rclpy
from rclpy.node import Node
from std_msgs.msg import String
from picamera2 import Picamera2


# ── CONFIG (reprise de camera_service.py) ──
MARKER_SIZE_M = 0.077
DISTANCE_CORRECTION = 0.738
RESOLUTION = (640, 480)

ROOT = "/home/pi/MARC"
WEB_DIR = os.path.join(ROOT, "web")
CALIB_FILE = os.path.join(ROOT, "vision", "camera_calibration.npz")


# ── État partagé entre la boucle caméra, ROS2 et Flask ──
state_lock = threading.Lock()
latest_detections = {}     # {id: {distance, angle}}
latest_frame_jpeg = None   # dernier JPEG annoté (pour /video)


class CameraNode(Node):
    def __init__(self):
        super().__init__("camera_node")
        self.pub_detections = self.create_publisher(String, "/aruco/detections", 10)

        # Publie les détections à 10 Hz depuis l'état partagé
        self.create_timer(0.1, self.publish_detections)

        self.get_logger().info("camera_node démarré")

    def publish_detections(self):
        with state_lock:
            dets = dict(latest_detections)
        msg = String()
        msg.data = json.dumps(dets)
        self.pub_detections.publish(msg)


# ─────────────────────────────────────────────
#  BOUCLE CAMÉRA (thread) — reprise de camera_service.camera_loop
# ─────────────────────────────────────────────
def camera_loop(logger):
    global latest_detections, latest_frame_jpeg

    # Calibration (optionnelle)
    calib_ok = False
    camera_matrix = None
    dist_coeffs = None
    if os.path.exists(CALIB_FILE):
        calib = np.load(CALIB_FILE)
        camera_matrix = calib["camera_matrix"]
        dist_coeffs = calib["dist_coeffs"]
        calib_ok = True
        logger.info(f"Calibration chargée (erreur {calib['mean_error']:.3f} px)")
    else:
        logger.warn("Pas de calibration — distances indisponibles")

    # Capture via OpenCV/V4L2 (libcamera 0.2.0 d'Ubuntu Noble est buggé sur cette IMX219,
    # on contourne en passant par le pilote V4L2 standard).
    # Remplacer tout le bloc VideoCapture par :
    picam2 = Picamera2()
    picam2.configure(picam2.create_video_configuration(
        main={"size": RESOLUTION, "format": "RGB888"}))
    picam2.start()
    time.sleep(2)
    logger.info(f"Caméra démarrée via Picamera2 ({RESOLUTION[0]}x{RESOLUTION[1]})")

    aruco_dict = aruco.getPredefinedDictionary(aruco.DICT_4X4_50)
    aruco_params = aruco.DetectorParameters()

    marker_pts = np.array([
        [-MARKER_SIZE_M/2,  MARKER_SIZE_M/2, 0],
        [ MARKER_SIZE_M/2,  MARKER_SIZE_M/2, 0],
        [ MARKER_SIZE_M/2, -MARKER_SIZE_M/2, 0],
        [-MARKER_SIZE_M/2, -MARKER_SIZE_M/2, 0],
    ], dtype=np.float32)

    while True:
        frame = picam2.capture_array()
        # frame est en RGB, donc remplacer COLOR_BGR2GRAY par COLOR_RGB2GRAY
        gray = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
        # Et avant imencode, convertir RGB -> BGR pour OpenCV
        frame_bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
        # OpenCV renvoie déjà du BGR. La calibration a été faite avec une image
        # passée en COLOR_RGB2GRAY (camera_service.py utilisait picamera2 en RGB888).
        # On garde la même chaîne : on convertit BGR → RGB pour grayscale, puis on
        # travaille sur frame_bgr pour l'annotation.
        if frame_bgr.shape[1] != RESOLUTION[0] or frame_bgr.shape[0] != RESOLUTION[1]:
            frame_bgr = cv2.resize(frame_bgr, RESOLUTION)

        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        corners, ids, _ = aruco.detectMarkers(gray, aruco_dict, parameters=aruco_params)

        detections = {}

        if ids is not None:
            aruco.drawDetectedMarkers(frame_bgr, corners, ids)
            for i, mid in enumerate(ids.flatten()):
                entry = {"distance": None, "angle": None}
                if calib_ok:
                    ok, rvec, tvec = cv2.solvePnP(
                        marker_pts, corners[i][0], camera_matrix, dist_coeffs)
                    if ok:
                        distance = float(np.linalg.norm(tvec)) * DISTANCE_CORRECTION
                        angle = math.degrees(math.atan2(tvec[0][0], tvec[2][0]))
                        entry = {"distance": round(distance, 3), "angle": round(angle, 1)}
                        cv2.drawFrameAxes(frame_bgr, camera_matrix, dist_coeffs,
                                          rvec, tvec, MARKER_SIZE_M/2)
                        c = corners[i][0][0]
                        label = f"ID{mid} {distance:.2f}m {angle:+.0f}deg"
                        cv2.putText(frame_bgr, label, (int(c[0]), int(c[1]) - 10),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
                else:
                    c = corners[i][0][0]
                    cv2.putText(frame_bgr, f"ID{mid}", (int(c[0]), int(c[1]) - 10),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
                detections[int(mid)] = entry

        _, buf = cv2.imencode(".jpg", frame_bgr, [cv2.IMWRITE_JPEG_QUALITY, 80])

        with state_lock:
            latest_detections = detections
            latest_frame_jpeg = buf.tobytes()


# ─────────────────────────────────────────────
#  SERVEUR FLASK MJPEG (thread) — pour l'interface web
# ─────────────────────────────────────────────
flask_app = Flask(__name__)


@flask_app.route("/video")
def video():
    def gen():
        while True:
            with state_lock:
                frame = latest_frame_jpeg
            if frame:
                yield (b"--frame\r\n"
                       b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n")
            time.sleep(0.04)
    return Response(gen(), mimetype="multipart/x-mixed-replace; boundary=frame")


@flask_app.route("/detections")
def detections_http():
    with state_lock:
        return jsonify(latest_detections)


@flask_app.route("/health")
def health():
    return jsonify({"marker_size_m": MARKER_SIZE_M})


def run_flask():
    cert = os.path.join(WEB_DIR, "cert.pem")
    key = os.path.join(WEB_DIR, "key.pem")
    print("[CAM] Service MJPEG sur https://0.0.0.0:5001")
    flask_app.run(host="0.0.0.0", port=5001, threaded=True,
                  use_reloader=False, ssl_context=(cert, key))


# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────
def main(args=None):
    rclpy.init(args=args)
    node = CameraNode()

    # Boucle caméra + serveur MJPEG dans des threads daemon
    threading.Thread(target=camera_loop, args=(node.get_logger(),), daemon=True).start()
    threading.Thread(target=run_flask, daemon=True).start()

    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
