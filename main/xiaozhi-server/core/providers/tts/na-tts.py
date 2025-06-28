import base64
import os
import uuid
import requests
from pathlib import Path
from pydantic import BaseModel, Field, conint, model_validator
from typing_extensions import Annotated
from datetime import datetime
from typing import Literal
import json
# from base import TTSProviderBase
from core.providers.tts.base import TTSProviderBase  # 请确保导入路径正确

class TTSProvider(TTSProviderBase):
    def __init__(self, config, delete_audio_file):
        super().__init__(config, delete_audio_file)
        
        # 从配置中获取参数
        self.voice_id = config.get("voice_id", 1917)  # 默认纳西妲角色
        self.tts_url = config.get("tts_url", "https://u95167-bd74-2aef8085.westx.seetacloud.com:8443/flashsummary/tts")
        self.retrieve_url = config.get("retrieve_url", "https://u95167-9b1c-2697c52f.bjc1.seetacloud.com:8443/flashsummary/retrieveFileData")
        self.audio_format = config.get("format", "mp3")
        self.headers = config.get("headers", {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
        })

    def generate_filename(self, extension=".mp3"):
        """生成唯一的音频文件名"""
        return os.path.join(
            self.output_file, 
            f"tts-{datetime.now().strftime('%Y%m%d%H%M%S')}@{uuid.uuid4().hex}{extension}"
        )

    async def text_to_speak(self, text, output_file=None):
        """
        执行TTS转换的核心方法
        :param text: 需要转换的文本
        :param output_file: 输出文件路径（可选）
        :return: 生成的音频文件路径
        """
        # 生成输出路径
        if not output_file:
            output_file = self.generate_filename(extension=f".{self.audio_format}")

        # 构建请求体
        request_body = {
            "voice_id": self.voice_id,
            "text": text,
            "format": self.audio_format,
            "to_lang": "ZH",
            "auto_translate": 0,
            "voice_speed": "0%",
            "speed_factor": 1,
            "pitch_factor": 0,
            "rate": "1.0",
            "client_ip": "ACGN",
            "emotion": 1
        }

        try:
            # 第一步：发送TTS生成请求
            tts_response = requests.post(
                self.tts_url,
                headers=self.headers,
                data=json.dumps(request_body)
            )
            
            if tts_response.status_code != 200:
                raise Exception(f"TTS请求失败，状态码: {tts_response.status_code}")
                
            voice_path = tts_response.json().get("voice_path")
            if not voice_path:
                raise Exception("响应中未找到voice_path")

            # 第二步：获取音频文件
            audio_url = f"{self.retrieve_url}?stream=True&token=null&voice_audio_path={voice_path}"
            audio_response = requests.get(audio_url, headers=self.headers)
            
            if audio_response.status_code != 200:
                raise Exception(f"音频下载失败，状态码: {audio_response.status_code}")

            # 保存音频文件
            with open(output_file, "wb") as f:
                f.write(audio_response.content)

            return output_file

        except Exception as e:
            if self.delete_audio_file and os.path.exists(output_file):
                os.remove(output_file)
            raise e

# 示例配置和使用方式
if __name__ == "__main__":
    config = {
        "voice_id": 1917,  # 角色ID
        "tts_url": "https://your-tts-endpoint.com/tts",
        "retrieve_url": "https://your-retrieve-endpoint.com/retrieve",
        "format": "mp3",
        "output_file": "./audio_output",  # 输出目录
        "headers": {
            "Accept": "application/json",
            "Content-Type": "application/json"
        }
    }
    
    tts_provider = TTSProvider(config, delete_audio_file=True)
    
    # 异步调用示例（需要在外层使用async）
    # import asyncio
    # async def main():
    #     audio_path = await tts_provider.text_to_speak("测试文本")
    #     print(f"生成的音频文件: {audio_path}")
    # asyncio.run(main())