from config.logger import setup_logging
import os
import re
import time
import random
import asyncio
import difflib
import traceback
from pathlib import Path
from core.utils import p3
from core.handle.sendAudioHandle import send_stt_message
from plugins_func.register import register_function, ToolType, ActionResponse, Action
from core.providers.tts.dto.dto import TTSMessageDTO, SentenceType, ContentType
from core.utils.dialogue import Message
import requests
from pydub import AudioSegment
from core.handle.sendLyricsHandle import start_lyrics_sync
from core.handle.saveLyricsHandle import save_lyrics

TAG = __name__

MUSIC_CACHE = {}

play_music_function_desc = {
    "type": "function",
    "function": {
        "name": "play_online_music",
        "description": "在线唱歌、在线听歌、在线播放音乐的方法。",
        "parameters": {
            "type": "object",
            "properties": {
                "song_name": {
                    "type": "string",
                    "description": "歌曲名称，如果用户没有指定具体歌名或者用户只说了指令没有说歌名，则返回默认音乐'晴天', 明确指定的时返回音乐的名字 示例: ```用户:在线播放两只老虎\n参数：两只老虎``` ```用户:播放在线音乐 \n参数：晴天 ```  ```用户:QQ点歌晴天 \n参数：晴天 ```",
                }
            },
            "required": ["song_name"],
        },
    },
}


@register_function("play_online_music", play_music_function_desc, ToolType.SYSTEM_CTL)
def play_music(conn, song_name: str):
    try:
        music_intent = (
            f"播放音乐 {song_name}" if song_name != "random" else "随机播放音乐"
        )

        # 检查事件循环状态
        if not conn.loop.is_running():
            conn.logger.bind(tag=TAG).error("事件循环未运行，无法提交任务")
            return ActionResponse(
                action=Action.RESPONSE, result="系统繁忙", response="请稍后再试"
            )

        # 提交异步任务
        future = asyncio.run_coroutine_threadsafe(
            handle_music_command(conn, music_intent), conn.loop
        )

        # 获取歌曲文件的绝对路径
        specific_file = "music"

        # # 启动歌词线程
        # lyric_future = asyncio.create_task(start_lyrics_sync(conn, specific_file))
        # conn.logger.bind(tag=TAG).info("开始处理歌词")

        # 非阻塞回调处理
        def handle_done(f):
            try:
                f.result()  # 可在此处理成功逻辑
                conn.logger.bind(tag=TAG).info("播放完成")
            except Exception as e:
                conn.logger.bind(tag=TAG).error(f"播放失败: {e}")

        # 非阻塞歌词回调处理
        # def lyrics_handle_done(f):
        #     try:
        #         f.result()  # 可在此处理成功逻辑
        #         conn.logger.bind(tag=TAG).info("歌词推送完成")
        #     except Exception as e:
        #         conn.logger.bind(tag=TAG).error(f"歌词推送失败: {e}")

        future.add_done_callback(handle_done)
        # lyric_future.add_done_callback(lyrics_handle_done)

        # return ActionResponse(
        #     action=Action.NONE, result="指令已接收", response=""
        # )
    except Exception as e:
        conn.logger.bind(tag=TAG).error(f"处理音乐意图错误: {e}")
        return ActionResponse(
            action=Action.RESPONSE, result=str(e), response="播放音乐时出错了"
        )


def _extract_song_name(text):
    """从用户输入中提取歌名"""
    for keyword in ["播放音乐"]:
        if keyword in text:
            parts = text.split(keyword)
            if len(parts) > 1:
                return parts[1].strip()
    return None


def _find_best_match(potential_song, music_files):
    """查找最匹配的歌曲（增强版）"""
    best_match = None
    highest_score = 0
    potential_song = re.sub(r'[^\w\s]', '', potential_song).lower()

    for music_file in music_files:
        song_name = os.path.splitext(music_file)[0]
        clean_name = re.sub(r'[^\w\s]', '', song_name).lower()
        
        # 使用组合相似度算法
        seq_ratio = difflib.SequenceMatcher(None, potential_song, clean_name).ratio()
        partial_ratio = difflib.SequenceMatcher(None, potential_song, clean_name).quick_ratio()
        score = (seq_ratio * 0.6 + partial_ratio * 0.4)  # 组合权重
        
        # 增加绝对匹配检测
        if potential_song in clean_name or clean_name in potential_song:
            score = max(score, 0.85)
            
        if score > highest_score and score > 0.6:  # 提升阈值到0.6
            highest_score = score
            best_match = music_file
            conn.logger.bind(tag=TAG).debug(f"新最佳匹配: {song_name} 得分: {score:.2f}")
    
    return best_match if highest_score >= 0.6 else None


def get_music_files(music_dir, music_ext):
    music_dir = Path(music_dir)
    music_files = []
    music_file_names = []
    for file in music_dir.rglob("*"):
        # 判断是否是文件
        if file.is_file():
            # 获取文件扩展名
            ext = file.suffix.lower()
            # 判断扩展名是否在列表中
            if ext in music_ext:
                # 添加相对路径
                music_files.append(str(file.relative_to(music_dir)))
                music_file_names.append(
                    os.path.splitext(str(file.relative_to(music_dir)))[0]
                )
    return music_files, music_file_names


def initialize_music_handler(conn):
    global MUSIC_CACHE
    if MUSIC_CACHE == {}:
        if "play_music" in conn.config["plugins"]:
            MUSIC_CACHE["music_config"] = conn.config["plugins"]["play_music"]
            MUSIC_CACHE["music_dir"] = os.path.abspath(
                MUSIC_CACHE["music_config"].get("music_dir", "./music")  # 默认路径修改
            )
            MUSIC_CACHE["music_ext"] = MUSIC_CACHE["music_config"].get(
                "music_ext", (".mp3", ".wav", ".p3")
            )
            MUSIC_CACHE["refresh_time"] = MUSIC_CACHE["music_config"].get(
                "refresh_time", 60
            )
        else:
            MUSIC_CACHE["music_dir"] = os.path.abspath("./music")
            MUSIC_CACHE["music_ext"] = (".mp3", ".wav", ".p3")
            MUSIC_CACHE["refresh_time"] = 60
        # 获取音乐文件列表
        MUSIC_CACHE["music_files"], MUSIC_CACHE["music_file_names"] = get_music_files(
            MUSIC_CACHE["music_dir"], MUSIC_CACHE["music_ext"]
        )
        MUSIC_CACHE["scan_time"] = time.time()
        MUSIC_CACHE["music_cache_dir"] = os.path.abspath("./music/cache")
        os.makedirs(MUSIC_CACHE["music_cache_dir"], exist_ok=True)
        MUSIC_CACHE["download_api"]  = "http://datukuai.top:1450/djs/API/QQ_Music/api.php"
    return MUSIC_CACHE

def _detect_audio_type(file_path):
    """通过文件头检测音频类型（增强版）"""
    max_head_size = 4096  # 读取4KB内容进行检测
    with open(file_path, 'rb') as f:
        head = f.read(max_head_size)
        
        # MP3检测（ID3v1/v2标签）
        if head.startswith(b'ID3'):
            return 'mp3'
        
        # M4A检测（QuickTime文件格式）
        if head.startswith(b'ftyp'):
            return 'm4a'
        
        # WAV检测
        if head.startswith(b'RIFF'):
            return 'wav'
        
        # AAC检测（ADTS头部）
        if head.startswith(b'\x00\x00\x00\x1f\x61\x74\x64\x53'):
            return 'aac'
        
        # 其他流媒体格式检测
        # 继续检查常见的流媒体头部特征
        # FFV1视频流（虽然不是音频，但某些情况可能出现）
        if head.startswith(b'FFV1'):
            return 'unknown'  # 视为未知流媒体
        
        # 如果仍未检测到，继续扫描剩余内容
        # 查找MP3的魔数（可能在文件中间）
        mp3_signature = b'\x49\x44\x33'  # "ID3"
        pos = 0
        while pos < len(head) - 3:
            if head[pos:pos+3] == mp3_signature:
                return 'mp3'
            pos += 1
        
        # 检查MPEG-4音频流
        mpeg4_signature = b'\x00\x00\x01'  # ISO BMFF标识符
        if head.find(mpeg4_signature) != -1:
            return 'm4a'
        
        return None

def _validate_download(temp_path, expected_size):
    """验证下载文件完整性"""
    if not os.path.exists(temp_path):
        return False
    downloaded_size = os.path.getsize(temp_path)
    if downloaded_size < expected_size * 0.9:  # 允许一定误差
        return False
    return True

async def play_online_music(conn, specific_file=None, song_name=None):
    """播放在线音乐文件"""
    try:

        # 原有播放逻辑
        selected_music = specific_file
        music_path = os.path.join(MUSIC_CACHE["music_dir"], selected_music)
        conn.logger.bind(tag=TAG).info(f"音乐文件绝对路径{music_path}")
        conn.tts_first_text = selected_music
        conn.tts_last_text = selected_music
        conn.llm_finish_task = True
        
        status = f"正在播放歌曲: {song_name}"
        text = f"《{song_name}》"
        await send_stt_message(conn, text)
        conn.logger.bind(tag=TAG).info(status)
        conn.tts_last_text_index = 0
        conn.tts_first_text_index = 0

        conn.tts.tts_text_queue.put(
            TTSMessageDTO(
                sentence_id=conn.sentence_id,
                sentence_type=SentenceType.MIDDLE,
                content_type=ContentType.FILE,
                content_file=music_path,
            )
        )
        conn.tts.tts_text_queue.put(
            TTSMessageDTO(
                sentence_id=conn.sentence_id,
                sentence_type=SentenceType.LAST,
                content_type=ContentType.ACTION,
            )
        )
        # 启动歌词线程
        asyncio.create_task(start_lyrics_sync(conn, specific_file))
        conn.logger.bind(tag=TAG).info("开始处理歌词")


    except Exception as e:
        conn.logger.bind(tag=TAG).error(f"播放在线音乐失败: {str(e)}")
        conn.logger.bind(tag=TAG).error(f"详细错误: {traceback.format_exc()}")

def _cleanup_files(conn, file_paths):
    """清理指定的文件"""
    for path in file_paths:
        if os.path.exists(path):
            try:
                os.remove(path)
                conn.logger.bind(tag=TAG).info(f"清理文件: {path}")
            except Exception as e:
                conn.logger.bind(tag=TAG).error(f"清理文件失败: {path} - {str(e)}")

def convert_to_mp3(conn, input_path):
    """将音频文件转换为MP3格式（增强版）"""
    try:
        if input_path.endswith('.m4a'):
            audio = AudioSegment.from_file(input_path, format='m4a')
            output_path = os.path.join(MUSIC_CACHE["music_cache_dir"], f"{os.path.basename(input_path)}.mp3")
            audio.export(output_path, format='mp3', bitrate='192k')
            return output_path
        elif input_path.endswith('.aac'):
            audio = AudioSegment.from_file(input_path, format='aac')
            output_path = os.path.join(MUSIC_CACHE["music_cache_dir"], f"{os.path.basename(input_path)}.mp3")
            audio.export(output_path, format='mp3', bitrate='192k')
            return output_path
        elif input_path.endswith('.mp3'):
            return input_path
        else:
            raise ValueError(f"不支持的音频格式: {os.path.splitext(input_path)[1]}")
    except Exception as e:
        _cleanup_files(conn, [input_path])
        raise e
async def handle_online_song_command(conn, song_name):
    """处理在线点歌指令"""
    try:
        processed_song_name = re.sub(r'[^\u4e00-\u9fa5a-zA-Z0-9_]', '', song_name.strip()) or "unknown"
        
        # 优化1：建立双重匹配机制
        music_cache_files = [f for f in os.listdir(MUSIC_CACHE["music_cache_dir"]) if f.endswith('.mp3')]
        if music_cache_files:
            # 初始化匹配变量
            best_score = -1
            best_match = None
            
            # 解析缓存文件名中的歌手和歌曲主体
            cache_items = []
            for f in music_cache_files:
                base_name = os.path.splitext(f)[0]
                if ' - ' in base_name:
                    singer, song_part = base_name.split(' - ', 1)
                else:
                    singer, song_part = '', base_name
                # 提取关键词（保留前3个中文字符）
                keywords = re.findall(r'[\u4e00-\u9fa5]{1,3}', song_part)
                conn.logger.bind(tag=TAG).debug(f"文件解析: {f} → 歌手:{singer}, 歌曲主体:{song_part}, 关键词:{keywords}")
                cache_items.append({
                    'file': f,
                    'singer': singer,
                    'song_keywords': keywords,
                    'full_name': base_name
                })
            
            # 用户输入关键词提取
            # 匹配至少2个连续有效字符
            input_keywords = re.findall(r'[\u4e00-\u9fa5a-zA-Z0-9]{2,}', processed_song_name)  
            input_keywords = input_keywords[:5]  # 扩展至前5个核心词
            
            # 在缓存项循环中添加权重衰减因子
            for i, item in enumerate(cache_items):
                # 时间衰减因子（优先匹配新缓存）
                time_factor = 0.9 ** (len(cache_items) - i)  
                # 计算关键词匹配得分时加入衰减因子
                keyword_score = sum(0.8 ** idx for idx, kw in enumerate(input_keywords) if kw in item['song_keywords']) * time_factor
                conn.logger.bind(tag=TAG).debug(f"文件匹配: {item['file']} → 关键词匹配数:{keyword_score}")
                # 计算模糊匹配得分
                full_match_score = difflib.SequenceMatcher(None, processed_song_name, item['full_name']).ratio()
                conn.logger.bind(tag=TAG).debug(f"文件匹配: {item['file']} → 全称相似度:{full_match_score:.2f}")
                # 综合得分（关键词权重更高）
                total_score = keyword_score * 2 + full_match_score * 1
                conn.logger.bind(tag=TAG).debug(f"文件总得分: {item['file']} → {total_score:.2f}")
                if total_score > best_score:
                    best_score = total_score
                    best_match = item
            
            if best_match and best_score > 0.5:  # 降低阈值以适应简短输入
                mp3_cache_path = best_match['file']
                conn.logger.bind(tag=TAG).info(f"选择最佳缓存文件: {mp3_cache_path} (得分:{best_score:.2f})")
                display_name = best_match['full_name'].split(' - ', 1)[-1].strip() or "未知"
                text = f"正在播放在线歌曲: {display_name}"
                await send_stt_message(conn, text)
                dir_tmp = MUSIC_CACHE["music_cache_dir"]
                conn.logger.bind(tag=TAG).debug(f"歌曲文件名：{mp3_cache_path}。缓存路径: {dir_tmp}")
                mp3_path = os.path.join(MUSIC_CACHE["music_cache_dir"], f"{mp3_cache_path}")
                conn.logger.bind(tag=TAG).debug(f"传参：{mp3_path}, {song_name}")
                # 改用文件名做标题
                song_name = mp3_cache_path.replace('.mp3', '')
                await play_online_music(conn, specific_file=mp3_path, song_name=song_name)
                return True
        
        # 保持原有精确匹配逻辑
        mp3_cache_path = os.path.join(MUSIC_CACHE["music_cache_dir"], f"{processed_song_name}.mp3")            
        if os.path.exists(mp3_cache_path):
            conn.logger.bind(tag=TAG).info(f"该歌曲在本地缓存中，发送缓存歌曲: {song_name}")
            text = f"正在播放在线歌曲: {song_name}"
            await send_stt_message(conn, text)
            await play_online_music(conn, specific_file=mp3_cache_path, song_name=song_name)
            return True

        response = requests.get(MUSIC_CACHE["download_api"], params={'msg': song_name, 'n': 1}, timeout=10)
        response.raise_for_status()
        data = response.json()
        conn.logger.bind(tag=TAG).info(f"点歌API响应: {data}")
        error_code = data.get('code')

        if error_code != 1:
            if error_code == -4:
                error_details = data.get('text', '')
                await send_stt_message(conn, "播放失败，该歌曲可能是会员专享歌曲。请尝试使用“在线播放歌手 - 歌曲名”来搜索歌曲，比如“在线播放周杰伦的晴天”")
                return False
            elif error_code == -1:
                error_details = data.get('text', '')
                await send_stt_message(conn, error_details)
                return False
            else:
                await send_stt_message(conn, "在线点歌API发生错误")
                raise Exception(f"API错误: {data['text']}")


        singer = data['data'].get('singer', '') or ''
        song = data['data'].get('song', '') or ''
        clean_singer = re.sub(r'[^\u4e00-\u9fa5a-zA-Z0-9_]', '', singer.strip())
        clean_song = re.sub(r'[^\u4e00-\u9fa5a-zA-Z0-9_]', '', song.strip())
        processed_song_name = f"{clean_singer} - {clean_song}".strip() or "unknown"
        api_song_name = data['data'].get('song', '') or _extract_song_name(song_name)
        music_url = data['data']['music']
        temp_cache_path = os.path.join(MUSIC_CACHE["music_cache_dir"], f"{processed_song_name}.tmp")
        response = requests.get(music_url, stream=True, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
        response.raise_for_status()

        conn.logger.bind(tag=TAG).info("开始处理歌词")
        # 获取音乐ID
        music_mid = data['data'].get('mid', '') or ""
        if music_mid:
            # 如果获取到mid，调用saveLyricsHandle中的函数
            conn.logger.bind(tag=TAG).info(f"获取到mid成功: {music_mid}，开始下载歌词")
            asyncio.create_task(save_lyrics(conn,song_id=music_mid, id_type='mid', lyrics_dir=MUSIC_CACHE["music_cache_dir"], file_name=processed_song_name))
        else:
            # 否则，改为获取songid
            conn.logger.bind(tag=TAG).info(f"未获取到mid，改为获取songid")
            music_mid = str(data['data'].get('songid', ''))
            if music_mid:
                conn.logger.bind(tag=TAG).info(f"获取到songid成功: {music_mid}，开始下载歌词")
                asyncio.create_task(save_lyrics(conn,song_id=music_mid, id_type='id', lyrics_dir=MUSIC_CACHE["music_cache_dir"], file_name=processed_song_name))
            else:
                conn.logger.bind(tag=TAG).info(f"未获取到songid，跳过获取。")

        expected_size = int(response.headers.get('Content-Length', 0))
        downloaded_size = 0
        with open(temp_cache_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
                downloaded_size += len(chunk)
        
        if not _validate_download(temp_cache_path, expected_size):
            raise Exception("文件下载不完整")

        audio_type = _detect_audio_type(temp_cache_path)
        if not audio_type:
            raise ValueError("未知音频格式")

        # 构建最终缓存文件路径
        cache_filename = f"{processed_song_name}.{audio_type}"
        final_cache_path = os.path.join(MUSIC_CACHE["music_cache_dir"], cache_filename)
        os.makedirs(os.path.dirname(final_cache_path), exist_ok=True)

        # 强制覆盖已存在的文件
        if os.path.exists(final_cache_path):
            os.remove(final_cache_path)
        os.rename(temp_cache_path, final_cache_path)
        conn.logger.bind(tag=TAG).info(f"覆盖已存在的缓存文件: {final_cache_path}")

        # 处理音频格式转换
        if audio_type != 'mp3':
            converted_path = convert_to_mp3(conn, final_cache_path)
            if not converted_path:
                raise Exception("音频转换失败")
            
            # 删除原始非MP3文件
            os.remove(final_cache_path)
            
            # 构建目标MP3文件路径
            mp3_cache_path = os.path.join(
                MUSIC_CACHE["music_cache_dir"], 
                f"{processed_song_name}.mp3"
            )
            
            # 删除可能存在的同名MP3文件
            if os.path.exists(mp3_cache_path):
                os.remove(mp3_cache_path)
            
            # 重命名转换后的文件到最终位置
            os.rename(converted_path, mp3_cache_path)
            final_cache_path = mp3_cache_path
            conn.logger.bind(tag=TAG).info(f"覆盖已存在的MP3缓存文件: {mp3_cache_path}")

        mp3_cache_path = os.path.join(MUSIC_CACHE["music_cache_dir"], f"{processed_song_name}.mp3")
        if final_cache_path != mp3_cache_path:
            raise ValueError("文件格式转换失败")

        text = f"正在播放在线歌曲: {api_song_name}"
        await send_stt_message(conn, text)
        temp_dir = MUSIC_CACHE["music_cache_dir"]
        conn.logger.bind(tag=TAG).info(f"歌曲文件名：{song_name}。缓存路径: {temp_dir}")
        conn.logger.bind(tag=TAG).info(f"传参：{mp3_cache_path}, {api_song_name}")
        await play_online_music(conn, specific_file=mp3_cache_path, song_name=api_song_name)
        
    except Exception as e:
        error_msg = f"在线点歌失败: {str(e)}"
        if isinstance(e, requests.exceptions.RequestException):
            error_msg += f"\n网络请求错误: {e.request.url} - {e.response.status_code}"
        elif isinstance(e, ValueError) and "文件已存在" in str(e):
            error_msg += f"\n文件冲突解决: {str(e)}"
        else:
            error_msg += f"\n文件处理错误: {str(e)}"
        conn.logger.bind(tag=TAG).error(error_msg)
        # 清理临时文件（安全检查）
        if 'temp_cache_path' in locals() and os.path.exists(temp_cache_path):  # 修改为安全检查
            os.remove(temp_cache_path)
            conn.logger.bind(tag=TAG).info(f"清理临时文件: {temp_cache_path}")
        # 如果转换过程中生成了中间文件也要清理
        if hasattr(e, 'converted_path') and os.path.exists(e.converted_path):
            os.remove(e.converted_path)
            conn.logger.bind(tag=TAG).info(f"清理转换文件: {e.converted_path}")
        await send_stt_message(conn, f"在线点歌失败，请稍后再试。错误详情: {str(e)}")
        return False

async def handle_music_command(conn, text):
    initialize_music_handler(conn)
    global MUSIC_CACHE

    """处理音乐播放指令"""
    clean_text = re.sub(r"[^\w\s]", "", text).strip()
    conn.logger.bind(tag=TAG).debug(f"检查是否是音乐命令: {clean_text}")

    song_name = _extract_song_name(clean_text)
    await handle_online_song_command(conn, song_name)
    return True

    # 尝试匹配具体歌名
    if os.path.exists(MUSIC_CACHE["music_dir"]):
        if time.time() - MUSIC_CACHE["scan_time"] > MUSIC_CACHE["refresh_time"]:
            # 刷新音乐文件列表
            MUSIC_CACHE["music_files"], MUSIC_CACHE["music_file_names"] = (
                get_music_files(MUSIC_CACHE["music_dir"], MUSIC_CACHE["music_ext"])
            )
            MUSIC_CACHE["scan_time"] = time.time()

        potential_song = _extract_song_name(clean_text)
        if potential_song:
            best_match = _find_best_match(potential_song, MUSIC_CACHE["music_files"])
            if best_match:
                conn.logger.bind(tag=TAG).info(f"找到最匹配的歌曲: {best_match}")
                await play_local_music(conn, specific_file=best_match)
                return True
    # 检查是否是通用播放音乐命令
    await play_local_music(conn)
    return True


def _get_random_play_prompt(song_name):
    """生成随机播放引导语"""
    # 移除文件扩展名
    clean_name = os.path.splitext(song_name)[0]
    prompts = [
        f"正在为您播放，{clean_name}",
        f"请欣赏歌曲，{clean_name}",
        f"即将为您播放，{clean_name}",
        f"为您带来，{clean_name}",
        f"让我们聆听，{clean_name}",
        f"接下来请欣赏，{clean_name}",
        f"为您献上，{clean_name}",
    ]
    # 直接使用random.choice，不设置seed
    return random.choice(prompts)


async def play_local_music(conn, specific_file=None):
    global MUSIC_CACHE
    """播放本地音乐文件"""
    try:
        if not os.path.exists(MUSIC_CACHE["music_dir"]):
            conn.logger.bind(tag=TAG).error(
                f"音乐目录不存在: " + MUSIC_CACHE["music_dir"]
            )
            return

        # 确保路径正确性
        if specific_file:
            selected_music = specific_file
            music_path = os.path.join(MUSIC_CACHE["music_dir"], specific_file)
        else:
            if not MUSIC_CACHE["music_files"]:
                conn.logger.bind(tag=TAG).error("未找到MP3音乐文件")
                return
            selected_music = random.choice(MUSIC_CACHE["music_files"])
            music_path = os.path.join(MUSIC_CACHE["music_dir"], selected_music)

        if not os.path.exists(music_path):
            conn.logger.bind(tag=TAG).error(f"选定的音乐文件不存在: {music_path}")
            return
        # tts 状态信息
        # text = _get_random_play_prompt(selected_music)
        # await send_stt_message(conn, text)
        # conn.dialogue.put(Message(role="assistant", content=text))
        # conn.tts_first_text_index = 0
        # conn.tts_last_text_index = 0

        # tts_file = await asyncio.to_thread(conn.tts.to_tts, text)
        # if tts_file is not None and os.path.exists(tts_file):
        #     conn.tts_last_text_index = 1
        #     opus_packets, _ = conn.tts.audio_to_opus_data(tts_file)
        #     conn.audio_play_queue.put((opus_packets, None, 0))
        #     os.remove(tts_file)

        conn.llm_finish_task = True

        if music_path.endswith(".p3"):
            opus_packets, _ = p3.decode_opus_from_file(music_path)
        else:
            opus_packets, _ = conn.tts.audio_to_opus_data(music_path)
        conn.audio_play_queue.put((opus_packets, None, conn.tts_last_text_index))

    except Exception as e:
        conn.logger.bind(tag=TAG).error(f"播放音乐失败: {str(e)}")
        conn.logger.bind(tag=TAG).error(f"详细错误: {traceback.format_exc()}")
        if best_match and best_score > 0.5:
            mp3_cache_path = best_match['file']
            conn.logger.bind(tag=TAG).info(f"选择最佳缓存文件: {mp3_cache_path} (得分:{best_score:.2f})")
            display_name = best_match['full_name'].split(' - ', 1)[-1].strip() or "未知"
            text = f"正在播放在线歌曲: {display_name}"
            await send_stt_message(conn, text)
            dir_tmp = MUSIC_CACHE["music_cache_dir"]
            conn.logger.bind(tag=TAG).debug(f"歌曲文件名：{mp3_cache_path}。缓存路径: {dir_tmp}")
            mp3_path = os.path.join(MUSIC_CACHE["music_cache_dir"], f"{mp3_cache_path}")
            conn.logger.bind(tag=TAG).debug(f"传参：{mp3_path}, {song_name}")
            # 改用文件名做标题
            song_name = mp3_cache_path.replace('.mp3', '')
            await play_online_music(conn, specific_file=mp3_path, song_name=song_name)
            return True
        
        # 保持原有精确匹配逻辑
        mp3_cache_path = os.path.join(MUSIC_CACHE["music_cache_dir"], f"{processed_song_name}.mp3")            
        if os.path.exists(mp3_cache_path):
            conn.logger.bind(tag=TAG).info(f"该歌曲在本地缓存中，发送缓存歌曲: {song_name}")
            text = f"正在播放在线歌曲: {song_name}"
            await send_stt_message(conn, text)
            await play_online_music(conn, specific_file=mp3_cache_path, song_name=song_name)
            return True

        response = requests.get(MUSIC_CACHE["download_api"], params={'msg': song_name, 'n': 1}, timeout=10)
        response.raise_for_status()
        data = response.json()
        conn.logger.bind(tag=TAG).info(f"点歌API响应: {data}")
        error_code = data.get('code')

        if error_code != 1:
            if error_code == -4:
                error_details = data.get('text', '')
                await send_stt_message(conn, "播放失败，该歌曲可能是会员专享歌曲。")
                return False
            elif error_code == -1:
                error_details = data.get('text', '')
                await send_stt_message(conn, error_details)
                return False
            else:
                await send_stt_message(conn, "在线点歌API发生错误")
                raise Exception(f"API错误: {data['text']}")

        singer = data['data'].get('singer', '') or ''
        song = data['data'].get('song', '') or ''
        clean_singer = re.sub(r'[^\u4e00-\u9fa5a-zA-Z0-9_]', '', singer.strip())
        clean_song = re.sub(r'[^\u4e00-\u9fa5a-zA-Z0-9_]', '', song.strip())
        processed_song_name = f"{clean_singer} - {clean_song}".strip() or "unknown"
        api_song_name = data['data'].get('song', '') or _extract_song_name(song_name)
        music_url = data['data']['music']
        temp_cache_path = os.path.join(MUSIC_CACHE["music_cache_dir"], f"{processed_song_name}.tmp")
        response = requests.get(music_url, stream=True, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
        response.raise_for_status()

        expected_size = int(response.headers.get('Content-Length', 0))
        downloaded_size = 0
        with open(temp_cache_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
                downloaded_size += len(chunk)
        
        if not _validate_download(temp_cache_path, expected_size):
            raise Exception("文件下载不完整")

        audio_type = _detect_audio_type(temp_cache_path)
        if not audio_type:
            raise ValueError("未知音频格式")

        # 构建最终缓存文件路径
        cache_filename = f"{processed_song_name}.{audio_type}"
        final_cache_path = os.path.join(MUSIC_CACHE["music_cache_dir"], cache_filename)
        os.makedirs(os.path.dirname(final_cache_path), exist_ok=True)

        # 强制覆盖已存在的文件
        if os.path.exists(final_cache_path):
            os.remove(final_cache_path)
        os.rename(temp_cache_path, final_cache_path)
        conn.logger.bind(tag=TAG).info(f"覆盖已存在的缓存文件: {final_cache_path}")

        # 处理音频格式转换
        if audio_type != 'mp3':
            converted_path = convert_to_mp3(conn, final_cache_path)
            if not converted_path:
                raise Exception("音频转换失败")
            
            # 删除原始非MP3文件
            os.remove(final_cache_path)
            
            # 构建目标MP3文件路径
            mp3_cache_path = os.path.join(
                MUSIC_CACHE["music_cache_dir"], 
                f"{processed_song_name}.mp3"
            )
            
            # 删除可能存在的同名MP3文件
            if os.path.exists(mp3_cache_path):
                os.remove(mp3_cache_path)
            
            # 重命名转换后的文件到最终位置
            os.rename(converted_path, mp3_cache_path)
            final_cache_path = mp3_cache_path
            conn.logger.bind(tag=TAG).info(f"覆盖已存在的MP3缓存文件: {mp3_cache_path}")

        mp3_cache_path = os.path.join(MUSIC_CACHE["music_cache_dir"], f"{processed_song_name}.mp3")
        if final_cache_path != mp3_cache_path:
            raise ValueError("文件格式转换失败")

        text = f"正在播放在线歌曲: {api_song_name}"
        await send_stt_message(conn, text)
        temp_dir = MUSIC_CACHE["music_cache_dir"]
        conn.logger.bind(tag=TAG).info(f"歌曲文件名：{song_name}。缓存路径: {temp_dir}")
        conn.logger.bind(tag=TAG).info(f"传参：{mp3_cache_path}, {api_song_name}")
        await play_online_music(conn, specific_file=mp3_cache_path, song_name=api_song_name)
        
    except Exception as e:
        error_msg = f"在线点歌失败: {str(e)}"
        if isinstance(e, requests.exceptions.RequestException):
            error_msg += f"\n网络请求错误: {e.request.url} - {e.response.status_code}"
        elif isinstance(e, ValueError) and "文件已存在" in str(e):
            error_msg += f"\n文件冲突解决: {str(e)}"
        else:
            error_msg += f"\n文件处理错误: {str(e)}"
        conn.logger.bind(tag=TAG).error(error_msg)
        # 清理临时文件（安全检查）
        if 'temp_cache_path' in locals() and os.path.exists(temp_cache_path):  # 修改为安全检查
            os.remove(temp_cache_path)
            conn.logger.bind(tag=TAG).info(f"清理临时文件: {temp_cache_path}")
        # 如果转换过程中生成了中间文件也要清理
        if hasattr(e, 'converted_path') and os.path.exists(e.converted_path):
            os.remove(e.converted_path)
            conn.logger.bind(tag=TAG).info(f"清理转换文件: {e.converted_path}")
        await send_stt_message(conn, f"在线点歌失败，请稍后再试。错误详情: {str(e)}")
        return False

