from moviepy import VideoFileClip
from PIL import Image

TARGET_W = 64
TARGET_H = 32
FPS = 15

video = VideoFileClip("C:\\Users\\jeanp\\Documents\\UDM_Year3\\PFE\\matrixLed\\EyeStyle3.mp4")

expressions = [
    ("neutral", 48.0, 50.0),
    ("neutral", 0.0, 1.0),
    ("suspicious", 1.5, 3.5),
    ("sad", 8.0, 10.0),
    ("angry", 6.0, 7.5),
    ("love", 12.0, 14.0),
    ("surprise", 14.0, 16.0),
    ("excited", 21.0, 24.0)
]

def resize_and_crop(frame):
    img = Image.fromarray(frame)

    w, h = img.size
    target_ratio = TARGET_W / TARGET_H
    current_ratio = w / h

    if current_ratio > target_ratio:
        # trop large → crop largeur
        new_w = int(h * target_ratio)
        left = (w - new_w) // 2
        img = img.crop((left, 0, left + new_w, h))
    else:
        # trop haut → crop hauteur
        new_h = int(w / target_ratio)
        top = (h - new_h) // 2
        img = img.crop((0, top, w, top + new_h))

    return img.resize((TARGET_W, TARGET_H), Image.NEAREST)

for name, start, end in expressions:
    clip = video.subclipped(start, end)

    frames = []
    for frame in clip.iter_frames(fps=FPS):
        frames.append(resize_and_crop(frame))

    frames[0].save(
        f"{name}.gif",
        save_all=True,
        append_images=frames[1:],
        duration=int(1000 / FPS),
        loop=0
    )

print("✅ GIFs 64x32 créés avec succès")
