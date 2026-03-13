import os
import re
import sys
import site
import subprocess
import json
import requests
import random

# 把 ffmpeg shared build 的 bin 目录加入 PATH
_script_dir = os.path.dirname(os.path.abspath(__file__))
_ffmpeg_bin = os.path.join(_script_dir, "ffmpeg-master-latest-win64-gpl-shared", "bin")
user_scripts_dir = os.path.join(site.USER_BASE, "Scripts")
os.environ["PATH"] = f"{_ffmpeg_bin}{os.pathsep}{user_scripts_dir}{os.pathsep}{os.environ.get('PATH', '')}"

from dotenv import load_dotenv
import db

load_dotenv() # 加载 .env 文件中的环境变量

# ===== API 配置 =====
def get_api_key():
    key = os.getenv("DASHSCOPE_API_KEY")
    if not key:
        raise ValueError("❌ 错误：未找到 API 密钥！请在界面左侧配置。")
    return key

MOBILE_UA = 'Mozilla/5.0 (Linux; Android 8.0.0; SM-G955U Build/R16NW) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Mobile Safari/537.36'
DOUYIN_HEADERS = {
    'user-agent': MOBILE_UA,
    'referer': 'https://www.douyin.com/?is_from_mobile_home=1&recommend=1'
}

# ===== 工具函数 =====

def run_cmd(cmd):
    print(f"👉 {cmd}")
    result = subprocess.run(cmd, shell=True, env=os.environ.copy())
    if result.returncode != 0:
        print(f"❌ 命令失败，退出码: {result.returncode}")
        return False
    return True

def extract_url(text):
    urls = re.findall(r'https?://\S+', text)
    return urls[0].rstrip('/') if urls else text.strip()

def resolve_video_id(short_url):
    resp = requests.get(short_url, headers=DOUYIN_HEADERS, allow_redirects=True, timeout=10)
    match = re.search(r'/video/(\d+)', resp.url)
    if match:
        return match.group(1)
    match = re.search(r'(\d{15,})', resp.url)
    return match.group(1) if match else None

# ===== 第1步：下载抖音视频 =====

def download_douyin_video(share_text, output_path="raw.mp4"):
    url = extract_url(share_text)
    print(f"🔗 链接: {url}")

    if 'v.douyin.com' in url or len(url) < 60:
        print("🔄 解析短链...")
        video_id = resolve_video_id(url)
    else:
        match = re.search(r'/video/(\d+)', url)
        video_id = match.group(1) if match else None

    if not video_id:
        print("❌ 无法提取 video_id")
        return False

    print(f"🎬 video_id: {video_id}")
    page_url = f"https://www.iesdouyin.com/share/video/{video_id}/"
    resp = requests.get(page_url, headers=DOUYIN_HEADERS, timeout=15)

    match = re.search(r'window\._ROUTER_DATA\s*=\s*(.*?)</script>', resp.text, re.DOTALL)
    if not match:
        print("❌ 页面解析失败")
        return False

    data = json.loads(match.group(1).strip().rstrip(';'))
    item = data['loaderData']['video_(id)/page']['videoInfoRes']['item_list'][0]
    video_uri = item['video']['play_addr']['uri']
    video_url = f'https://www.douyin.com/aweme/v1/play/?video_id={video_uri}'
    print(f"📝 标题: {item.get('desc', video_id)}")
    print("⬇️  下载中...")

    dl_resp = requests.get(video_url, headers={'user-agent': MOBILE_UA}, stream=True, timeout=60)
    with open(output_path, 'wb') as f:
        for chunk in dl_resp.iter_content(chunk_size=1024 * 1024):
            if chunk:
                f.write(chunk)

    size_mb = os.path.getsize(output_path) / 1024 / 1024
    print(f"✅ 下载完成：{output_path}（{size_mb:.1f} MB）")
    return True

# ===== 第2步：提取音频 =====

def extract_audio(video_path="raw.mp4", audio_path="temp_audio.wav"):
    print("🎵 提取音频...")
    return run_cmd(f'ffmpeg -i "{video_path}" -vn -acodec pcm_s16le -ar 16000 -ac 1 "{audio_path}" -y')

# ===== 第3步：上传音频到临时公网 + paraformer 异步转录 =====

def _upload_to_public(audio_path):
    """依次尝试多个免费临时文件托管，返回公网 URL；全部失败返回 None"""
    fname = os.path.basename(audio_path)

    # 尝试多个临时文件托管服务
    services = [
        # litterbox.catbox.moe（1小时有效）
        {
            "name": "litterbox",
            "url": "https://litterbox.catbox.moe/resources/internals/api.php",
            "data": {"reqtype": "fileupload", "time": "1h"},
            "files": lambda f: {"fileToUpload": (fname, f, "audio/wav")}
        },
        # 尝试另一个临时文件托管服务
        {
            "name": "tmpfiles",
            "url": "https://tmpfiles.org/api/v1/upload",
            "data": {},
            "files": lambda f: {"file": (fname, f, "audio/wav")}
        }
    ]

    for service in services:
        try:
            print(f"尝试上传到 {service['name']}...")
            with open(audio_path, 'rb') as f:
                resp = requests.post(
                    service["url"],
                    data=service["data"],
                    files=service["files"](f),
                    timeout=60
                )
            
            # 处理不同服务的响应格式
            if service["name"] == "litterbox":
                if resp.status_code == 200 and resp.text.startswith("https://"):
                    url = resp.text.strip()
                    print(f"✅ 音频已上传({service['name']})，URL: {url}")
                    return url
                print(f"⚠️  {service['name']} 失败({resp.status_code}): {resp.text[:100]}")
            elif service["name"] == "tmpfiles":
                if resp.status_code == 200:
                    try:
                        result = resp.json()
                        if result.get("success") and result.get("data", {}).get("url"):
                            url = result["data"]["url"]
                            print(f"✅ 音频已上传({service['name']})，URL: {url}")
                            return url
                    except:
                        pass
                    print(f"⚠️  {service['name']} 失败: 响应格式不正确")
        except Exception as e:
            print(f"⚠️  {service['name']} 异常: {e}")

    print("❌ 所有公网上传均失败")
    return None

def transcribe_audio(audio_path="temp_audio.wav"):
    print("🧠 上传音频到公网，准备转录...")

    audio_url = _upload_to_public(audio_path)
    if not audio_url:
        return None

    # 提交异步转录任务
    print("📝 提交转录任务...")
    task_resp = requests.post(
        "https://dashscope.aliyuncs.com/api/v1/services/audio/asr/transcription",
        headers={
            "Authorization": f"Bearer {get_api_key()}",
            "Content-Type": "application/json",
            "X-DashScope-Async": "enable"
        },
        json={
            "model": "paraformer-v2",
            "input": {"file_urls": [audio_url]},
            "parameters": {"language_hints": ["zh"], "timestamp_alignment": True}
        },
        timeout=30
    )
    if task_resp.status_code not in (200, 202):
        print(f"❌ 提交任务失败: {task_resp.text}")
        return None
    task_id = task_resp.json().get("output", {}).get("task_id")
    print(f"⏳ 任务已提交，task_id: {task_id}，等待结果...")

    # 轮询等待结果
    import time
    max_retries = 60  # 调整为60次，每次5秒，总共5分钟
    retry_count = 0
    while retry_count < max_retries:
        time.sleep(5)  # 增加每次等待时间到5秒
        retry_count += 1
        try:
            poll_resp = requests.get(
                f"https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}",
                headers={"Authorization": f"Bearer {get_api_key()}"},
                timeout=15  # 增加超时设置
            )
            if poll_resp.status_code != 200:
                print(f"⚠️  轮询请求失败，状态码: {poll_resp.status_code}")
                print(f"   响应内容: {poll_resp.text[:200]}")
                continue
            
            # 打印完整的响应内容以便调试
            print(f"   轮询响应: {poll_resp.text[:500]}")
            
            # 解析响应
            try:
                resp_json = poll_resp.json()
                status = resp_json.get("output", {}).get("task_status")
                print(f"   任务状态: {status}")
                
                if status == "SUCCEEDED":
                    print(f"✅ 转录任务成功完成，共等待 {retry_count*5} 秒")
                    break
                elif status == "FAILED":
                    error_msg = resp_json.get("output", {}).get("error", {}).get("message", "未知错误")
                    print(f"❌ 转录任务失败: {error_msg}")
                    print(f"   完整错误信息: {poll_resp.text}")
                    return None
                print(f"   状态: {status}... (等待 {retry_count*5} 秒)")
            except json.JSONDecodeError as e:
                print(f"⚠️  响应解析失败: {e}")
                print(f"   响应内容: {poll_resp.text[:200]}")
                continue
        except Exception as e:
            print(f"⚠️  轮询过程中发生错误: {e}")
            import traceback
            traceback.print_exc()
            # 继续轮询
    else:
        print("❌ 转录超时")
        print(f"   任务ID: {task_id}")
        print("   请检查阿里云百炼服务状态或尝试使用其他视频")
        return None

    # 4. 解析结果：先拿 transcription_url，再 fetch 详细 JSON
    try:
        results = poll_resp.json().get("output", {}).get("results", [])
        if not results:
            print(f"⚠️  转录结果为空: {poll_resp.text[:300]}")
            return None

        transcription_url = results[0].get("transcription_url")
        if not transcription_url:
            print(f"⚠️  未找到 transcription_url，原始结果: {results[0]}")
            return None

        print(f"📄 获取转录详情: {transcription_url}")
        detail_resp = requests.get(transcription_url, timeout=30)
        detail = detail_resp.json()
    except Exception as e:
        print(f"❌ 解析转录结果失败: {e}")
        import traceback
        traceback.print_exc()
        return None

    # 结构: transcripts[0].sentences[].{begin_time, end_time, text}
    sentences = detail.get("transcripts", [{}])[0].get("sentences", [])
    if not sentences:
        print(f"⚠️  sentences 为空，detail keys: {list(detail.keys())}")
        return None

    segments = [
        {"start": s["begin_time"] / 1000, "end": s["end_time"] / 1000, "text": s["text"]}
        for s in sentences
    ]
    print(f"✅ 转录完成，共 {len(segments)} 个句子")
    return segments

# ===== 第4步：阿里云百炼语义分段 =====

def semantic_split(segments):
    print("✂️  进行 AI 语义分段...")
    # 把字幕拼成文本，带时间戳
    lines = [f"[{s['start']:.2f}-{s['end']:.2f}] {s['text'].strip()}" for s in segments]
    transcript = "\n".join(lines)

    prompt = f"""你是一个视频剪辑助手。下面是一段视频的字幕（格式：[开始秒-结束秒] 文字）。
请按照语义和话题，把这段字幕分成若干个独立的短片段，每个片段15~60秒，内容完整、有意义。
输出 JSON 数组，每个元素包含：start（开始秒，数字）、end（结束秒，数字）、title（片段主题，10字以内）。
只输出 JSON，不要其他内容。

字幕内容：
{transcript}"""

    resp = requests.post(
        "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {get_api_key()}",
            "Content-Type": "application/json"
        },
        json={
            "model": "qwen-turbo",
            "messages": [{"role": "user", "content": prompt}]
        },
        timeout=60
    )
    if resp.status_code != 200:
        print(f"❌ 语义分段失败: {resp.text}")
        return None

    content = resp.json()["choices"][0]["message"]["content"].strip()
    # 提取 JSON
    match = re.search(r'\[.*\]', content, re.DOTALL)
    if not match:
        print(f"❌ 无法解析分段结果: {content}")
        return None

    clips = json.loads(match.group())
    print(f"✅ 分成 {len(clips)} 个片段")
    for i, c in enumerate(clips):
        print(f"   [{i+1}] {c['start']:.1f}s ~ {c['end']:.1f}s  {c['title']}")
    return clips

# ===== 第5步：切片 + 防重复处理 =====

def export_clips(clips, video_path="raw.mp4", segments=None, output_dir="output", orientation="portrait"):
    os.makedirs(output_dir, exist_ok=True)
    is_portrait = orientation == "portrait"
    print(f"\n🎬 开始导出 {len(clips)} 个片段（{'竖屏 9:16' if is_portrait else '横屏 16:9'}）...")

    exported = []
    for i, clip in enumerate(clips):
        start = clip['start']
        end = clip['end']
        title = re.sub(r'[\\/:*?"<>|]', '', clip.get('title', f'clip{i+1}'))
        out_path = os.path.join(output_dir, f"{i+1:02d}_{title}.mp4")

        # 随机防重复参数
        speed = round(random.uniform(0.97, 1.03), 3)
        crop_px = random.randint(2, 6)
        clip_frames = int((end - start) * 30)
        drop_pos1 = random.randint(1, max(2, clip_frames // 3))
        drop_pos2 = random.randint(clip_frames // 3 + 1, max(clip_frames // 3 + 2, clip_frames * 2 // 3))
        hue = random.uniform(-8, 8)
        saturation = round(random.uniform(0.95, 1.05), 2)
        brightness = round(random.uniform(-0.03, 0.03), 3)

        if is_portrait:
            ratio_crop = "crop=min(iw\\,ih*9/16):min(ih\\,iw*16/9):(iw-min(iw\\,ih*9/16))/2:(ih-min(ih\\,iw*16/9))/2"
            scale = "scale=1080:1920"
        else:
            ratio_crop = "crop=min(iw\\,ih*16/9):min(ih\\,iw*9/16):(iw-min(iw\\,ih*16/9))/2:(ih-min(ih\\,iw*9/16))/2"
            scale = "scale=1920:1080"

        vf_parts = [
            f"select='not(eq(n\\,{drop_pos1})+eq(n\\,{drop_pos2}))',setpts=N/FRAME_RATE/TB",
            f"crop=iw-{crop_px*2}:ih-{crop_px*2}:{crop_px}:{crop_px}",
            ratio_crop,
            scale,
            f"hue=h={hue:.1f}:s={saturation}",
            f"eq=brightness={brightness}"
        ]
        vf = ",".join(vf_parts)

        cmd = (
            f'ffmpeg -ss {start} -to {end} -i "{video_path}" '
            f'-vf "{vf}" '
            f'-af "atempo={speed}" '
            f'-c:v h264_nvenc -preset p6 -c:a aac -b:a 192k '
            f'"{out_path}" -y'
        )
        if run_cmd(cmd):
            print(f"   ✅ [{i+1}] {out_path}")
            exported.append((i, out_path, start, end, speed))
        else:
            print(f"   ❌ [{i+1}] 导出失败")

    if not exported:
        print("❌ 没有成功导出的片段")
        return

    # 合并
    concat_txt = os.path.join(output_dir, "concat.txt")
    with open(concat_txt, 'w', encoding='utf-8') as f:
        for _, p, *_ in exported:
            f.write(f"file '{os.path.abspath(p).replace(chr(92), '/')}'\n")
    merged_path = os.path.join(output_dir, "merged.mp4")
    print(f"\n🔗 合并 {len(exported)} 个片段 → {merged_path}")
    yield f"🔗 合并 {len(exported)} 个片段..."
    run_cmd(f'ffmpeg -f concat -safe 0 -i "{concat_txt}" -c copy "{merged_path}" -y')
    os.remove(concat_txt)

    # 删除分片，只保留 merged.mp4
    for _, p, *_ in exported:
        if os.path.exists(p):
            os.remove(p)

    # 写字幕 SRT（时间轴对齐到合并视频）
    if segments:
        srt_path = os.path.join(output_dir, "subtitles.srt")
        _write_merged_srt(segments, exported, srt_path)
        print(f"📝 字幕已保存: {srt_path}")
        
    return merged_path, exported

def _write_merged_srt(segments, exported, srt_path):
    """写合并视频的字幕，时间轴按各片段在合并视频中的偏移量对齐"""
    def fmt(s):
        h, m = int(s//3600), int((s%3600)//60)
        sec, ms = int(s%60), int((s-int(s))*1000)
        return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"

    with open(srt_path, 'w', encoding='utf-8') as f:
        idx = 1
        offset = 0.0  # 当前片段在合并视频中的起始时间
        for _, _, clip_start, clip_end, speed in exported:
            clip_dur = (clip_end - clip_start) / speed  # 变速后实际时长
            for seg in segments:
                if seg['end'] <= clip_start or seg['start'] >= clip_end:
                    continue
                t_start = (max(seg['start'], clip_start) - clip_start) / speed + offset
                t_end   = (min(seg['end'],   clip_end)   - clip_start) / speed + offset
                f.write(f"{idx}\n{fmt(t_start)} --> {fmt(t_end)}\n{seg['text'].strip()}\n\n")
                idx += 1
            offset += clip_dur

# ===== 主流程 (Generator 改造) =====

def process_video_gen(share_text, orientation="portrait"):
    yield "🔄 正在准备环境..."
    
    # 创建 SQLite 任务
    task_id = db.create_task(share_text)
    
    try:
        # 清理临时文件
        for tmp in ["raw.mp4", "temp_audio.wav"]:
            if os.path.exists(tmp):
                os.remove(tmp)

        # 1. 下载
        yield "⬇️ [1/4] 正在下载抖音原视频..."
        if not download_douyin_video(share_text, "raw.mp4"):
            db.update_task_status(task_id, "failed", "下载视频失败")
            yield "❌ 下载视频失败，请检查链接"
            return

        # 2. 提取音频
        yield "🎵 [2/4] 正在提取音频供 AI 分析..."
        if not extract_audio("raw.mp4", "temp_audio.wav"):
            db.update_task_status(task_id, "failed", "提取音频失败")
            yield "❌ 提取音频失败"
            return

        # 3. 转录
        yield "🧠 [3/4] 正在使用 AI 模型进行极其精准的声音转文字处理..."
        segments = transcribe_audio("temp_audio.wav")
        if not segments:
            db.update_task_status(task_id, "failed", "核心转文字模块失败")
            yield "❌ 核心声转字模块出现错误"
            return
        yield f"✅ 对话内容识别完毕，一共抓取出了 {len(segments)} 条自然对话"

        # 4. 语义分段
        yield "✂️ [4/4] 正在让最聪明的 AI 结合刚才听到的文字，为你筛选并打乱出极品的爆流片段..."
        clips = semantic_split(segments)
        if not clips:
            db.update_task_status(task_id, "failed", "AI 寻找起伏爆点失败")
            yield "❌ AI 寻找最佳起伏爆点时失败"
            return
        yield f"✅ AI 已经完成了导演工作，决定帮你切割出 {len(clips)} 个充满看点的精彩卡段"

        # 5. 导出
        output_dir = f"output/task_{task_id}"
        os.makedirs(output_dir, exist_ok=True)
        yield "🎬 开始最终混合与渲染，为你制作完全防搬运查重的最终组合长成片..."
        export_gen = export_clips(clips, "raw.mp4", segments=segments, output_dir=output_dir, orientation=orientation)
        
        # 处理 export_clips 的 generator (因为我们改成了 yield)
        if hasattr(export_gen, '__iter__') and not isinstance(export_gen, tuple):
             val = None
             try:
                 while True:
                     val = next(export_gen)
                     yield val
             except StopIteration as e:
                 # 获取 return 的结果
                 if e.value:
                     merged_path, exported_clips = e.value
                 else:
                     db.update_task_status(task_id, "failed", "渲染成品出图失败")
                     yield "❌ 渲染成品出图失败"
                     return
        else:
             db.update_task_status(task_id, "failed", "渲染模块内部错误")
             yield "❌ 渲染模块内部发生错误"
             return
             
        # 清除 raw.mp4 和 temp_audio.wav（因为没用了）
        for tmp in ["raw.mp4", "temp_audio.wav"]:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except:
                    pass
            
        srt_path = os.path.join(output_dir, "subtitles.srt")
        db.update_task_status(task_id, "success", video_path=merged_path, srt_path=srt_path)
        yield f"🎯 全部结束！成果视频和独立字幕文件已打包存放到专有文件夹。|{merged_path}|{srt_path}|{output_dir}"

    except Exception as e:
        db.update_task_status(task_id, "failed", str(e))
        yield f"❌ 遭遇严重异常: {str(e)}"

if __name__ == "__main__":
    if len(sys.argv) > 1:
        text = " ".join(sys.argv[1:])
    else:
        text = input("请粘贴抖音分享文案或链接: ")
    ori = input("视频方向（portrait=竖屏/landscape=横屏，默认竖屏）: ").strip() or "portrait"
    
    # 模拟网页前端迭代 generator
    for status_update in process_video_gen(text, orientation=ori):
        print(f"==> {status_update}")
