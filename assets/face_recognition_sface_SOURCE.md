SFace ONNX face recognition model

- Model: `face_recognition_sface_2021dec.onnx`
- Source: https://github.com/opencv/opencv_zoo/tree/main/models/face_recognition_sface
- License: Apache-2.0; see `face_recognition_sface_LICENSE.txt`
- SHA-256: `0BA9FBFA01B5270C96627C4EF784DA859931E02F04419C829E83484087C34E79`

Used only when the admin enables face recognition and the employee consents. The app stores
128-number face embeddings (not images). The model may misidentify faces under poor lighting,
strong angles or with low-quality cameras, so a mismatch is only flagged after several minutes.
