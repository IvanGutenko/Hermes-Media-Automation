import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

API_BASE = "https://apihub.agnes-ai.com"
DEFAULT_KEY = os.environ.get("AGNES_API_KEY", "")


def submit_task(api_key: str, prompt: str, seconds: str = "4", size: str = "720P", max_attempts: int = 15):
    url = f"{API_BASE}/v1/videos"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "agnes-video-2.5-flash",
        "prompt": prompt,
        "mode": "text",
        "seconds": seconds,
        "size": size,
        "aspect_ratio": "16:9",
    }

    print(f"Отправка задачи на генерацию видео...")
    print(f"Промпт: \"{prompt}\"")
    print(f"Параметры: модель=agnes-video-2.5-flash, длительность={seconds}с, разрешение={size}")

    for attempt in range(1, max_attempts + 1):
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                video_id = data.get("video_id") or data.get("id") or data.get("task_id")
                print(f"Попытка {attempt}: Успешно поставлено в очередь! Task ID: {video_id}")
                return video_id
        except urllib.error.HTTPError as e:
            err_text = e.read().decode("utf-8", errors="replace")
            print(f"Попытка {attempt}/{max_attempts}: HTTP {e.code} -> {err_text}")
            if "insufficient_user_quota" in err_text:
                raise RuntimeError(f"Недостаточно квоты: {err_text}")
        except Exception as e:
            print(f"Попытка {attempt}/{max_attempts}: Ошибка -> {e}")

        time.sleep(3)

    raise TimeoutError("Не удалось поставить задачу в очередь: очередь переполнена.")


def poll_and_download(api_key: str, video_id: str, output_path: str, poll_interval: int = 5):
    poll_url = f"{API_BASE}/agnesapi?video_id={video_id}&model_name=agnes-video-2.5-flash"
    headers = {"Authorization": f"Bearer {api_key}"}

    print(f"\nОтслеживание статуса генерации ({video_id})...")
    start_time = time.time()

    while True:
        try:
            req = urllib.request.Request(poll_url, headers=headers)
            with urllib.request.urlopen(req) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            status = data.get("status")
            progress = data.get("progress")
            if progress is None:
                progress = data.get("internal_progress", 0)
            url = data.get("url")

            elapsed = int(time.time() - start_time)
            print(f"[{elapsed}s] Статус: {status}, Прогресс: {progress}%")

            if status == "completed" or (url and url.strip()):
                print(f"\nГенерация завершена! URL видео: {url}")
                print(f"Скачивание в файл: {output_path}...")
                dl_req = urllib.request.Request(url)
                with urllib.request.urlopen(dl_req) as dl_resp, open(output_path, "wb") as f:
                    f.write(dl_resp.read())
                print(f"Файл успешно сохранен: {os.path.abspath(output_path)}")
                return output_path
            elif status in ("failed", "error"):
                err_msg = data.get("error")
                raise RuntimeError(f"Генерация завершилась с ошибкой: {err_msg}")

        except Exception as e:
            print(f"Ошибка при проверке статуса: {e}")

        time.sleep(poll_interval)


def main():
    parser = argparse.ArgumentParser(description="Agnes AI Video Generator")
    parser.add_argument("--prompt", type=str, default="A glowing neon jellyfish floating gracefully in deep ocean, cinematic lighting, 4k", help="Prompt for video")
    parser.add_argument("--video-id", type=str, default=None, help="Existing video task id to poll")
    parser.add_argument("--output", type=str, default="generated_video.mp4", help="Output file path")
    parser.add_argument("--api-key", type=str, default=os.getenv("AGNES_API_KEY", DEFAULT_KEY), help="API key")
    args = parser.parse_args()

    video_id = args.video_id
    if not video_id:
        video_id = submit_task(args.api_key, args.prompt)

    poll_and_download(args.api_key, video_id, args.output)


if __name__ == "__main__":
    main()
