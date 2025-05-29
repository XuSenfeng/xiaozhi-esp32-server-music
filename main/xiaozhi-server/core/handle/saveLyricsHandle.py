import os
import requests
from typing import Optional

async def save_lyrics(
    conn, 
    song_id: str, 
    id_type: str = 'mid',
    lyrics_dir: Optional[str] = None,
    file_name: Optional[str] = None
) -> bool:
    """
    保存歌词到文件
    :param song_id: 歌曲标识（mid或id）
    :param id_type: 标识类型，'mid' 或 'id'
    :param lyrics_dir: 自定义歌词目录路径（默认：data_dir/lyrics）
    :param file_name: 自定义歌词文件名（默认：{song_id}.lrc）
    :return: 是否保存成功
    """
    conn.logger.bind(tag=__name__).info(f"歌曲ID: {song_id}, ID类型: {id_type}, 歌词目录: {lyrics_dir}, 文件名: {file_name}")
    # 验证参数有效性
    if id_type not in ('mid', 'id'):
        conn.logger.bind(tag=__name__).error(f"无效的ID类型: {id_type}")
        return False

    try:
        # 构建请求URL
        base_url = "https://api.vkeys.cn/v2/music/tencent/lyric"
        # 修正参数名称为固定字段
        params = {'mid': song_id} if id_type == 'mid' else {'id': song_id}
        
        # 发送API请求
        response = requests.get(base_url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()

        # 验证响应格式
        if data.get('code') != 200 or 'data' not in data:
            conn.logger.bind(tag=__name__).error(f"API响应异常: {data}")
            return False

        # 提取并处理歌词内容
        lrc_content = data['data'].get('lrc', '')
        if not lrc_content:
            conn.logger.bind(tag=__name__).info("无歌词内容")
            return False

        # 处理换行和重复时间标签
        processed_lyrics = lrc_content.replace('\\n', '\n')  # 转换转义字符
        
        # 创建歌词目录
        if not lyrics_dir:
            lyrics_dir = os.path.join(conn.config['data_dir'], 'lyrics')
        os.makedirs(lyrics_dir, exist_ok=True)
        
        # 生成文件名
        final_name = f"{file_name}.lrc"
        conn.logger.bind(tag=__name__).info(f"歌词文件名：{final_name}")
        file_path = os.path.join(lyrics_dir, final_name)
        
        # 写入文件
        file_path = os.path.join(lyrics_dir, final_name)
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(processed_lyrics)
        
        conn.logger.bind(tag=__name__).info(f"歌词保存成功：{file_path}")
        return True

    except requests.exceptions.RequestException as e:
        conn.logger.bind(tag=__name__).error(f"API请求失败: {str(e)}")
    except Exception as e:
        conn.logger.bind(tag=__name__).exception("处理歌词时发生异常")
    
    return False