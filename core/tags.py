"""标签系统 — [key:value] 格式的编解码、Tag 模型与内置标签定义。

提供项目级的标签基础设施：
- 解析工具：tag_label, etag, etag_all, batch_remove_tags 等
- Tag 类：带名称和描述的标签模型
- 内置标签：time, uid, group_id, name, nickname, channel, media_file 等
"""

from __future__ import annotations

import datetime
import re
from typing import List, Optional, Tuple

from pydantic import BaseModel

# ======================================================================
# 解析工具
# ======================================================================

_tag_content_pattern = re.compile(r"^\[([^\]:]+):(.*)\]$")
_tag_extract_all_pattern = re.compile(r"\[((?:[^\\\]\[]|\\\]|\\\[|\\.)*)\]")


def tag_label(key: str, value: str) -> str:
    """拼接 tag：返回 ``[key:value]``。"""
    return f"[{key}:{value}]"


def etag(text: str) -> Tuple[str, str]:
    """提取单个 tag：输入应为形如 ``[key:value]`` 的字符串。"""
    matches = _tag_content_pattern.findall(text)
    if not matches:
        raise ValueError(f"非法 tag 文本: {text!r}")
    return matches[0]


def extract_tag_brackets(text: str) -> List[str]:
    """提取所有方括号及其内容（支持转义字符），返回形如 ``[xxx]`` 的片段列表。"""
    matches = _tag_extract_all_pattern.findall(text)
    return [f"[{m}]" for m in matches if m]


def etag_all(text: str) -> List[Tuple[str, str]]:
    """提取所有 tag（支持转义字符），跳过非 key:value 格式的方括号。"""
    text_tags = extract_tag_brackets(text)
    unique_tags: dict[Tuple[str, str], Tuple[str, str]] = {}
    for tag in text_tags:
        try:
            tag_tuple = etag(tag)
        except ValueError:
            continue
        unique_tags[(tag_tuple[0], tag_tuple[1])] = tag_tuple
    return list(unique_tags.values())


def batch_remove_tags(text: str) -> str:
    """批量移除所有标签（将 ``[k:v]`` 替换成 ``v``，保留值部分）。"""
    return re.sub(r"\[(?:[^:]+):(.*?)\]", r"\1", text, flags=re.DOTALL)


def get_current_time(time_format: str = "%Y年%m月%d日%H时%M分%S秒") -> str:
    """返回当前时间的格式化字符串。"""
    return datetime.datetime.now().strftime(time_format)


def get_time_tag() -> str:
    """返回当前时间标签 ``[time:...]``。"""
    return tag_label("time", get_current_time())


async def rm_unless_text(text: str) -> str:
    """清理 LLM 输出文本：移除 ``<think>...</think>`` 并去除首尾空白。"""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    return text.strip()


def try_get_tag_value(tags: List[Tuple[str, str]], key: str) -> Optional[str]:
    """在解析后的 tags 列表中查找某个 key 的值。"""
    for k, v in tags:
        if k == key:
            return v
    return None


# ======================================================================
# Tag 模型
# ======================================================================

tag_list: List["Tag"] = []


class Tag(BaseModel):
    """标签定义 — 将上下文信息以 ``[key:value]`` 注入对话文本。

    visible_to_llm 控制该标签描述是否注入 LLM 系统提示。
    消息上下文标签设为 True（LLM 需要理解 [key:value] 含义），
    纯工具路由标签设为 False（仅用于 PFC 内部调度）。
    """

    tag_name: str = ""
    tag_name_desc: str = ""
    visible_to_llm: bool = True

    def model_post_init(self, __context: object) -> None:
        tag_list.append(self)

    def get_tag_name(self) -> str:
        return self.tag_name

    def get_tag_desc(self) -> str:
        return f"{self.tag_name}标签表示{self.tag_name_desc} "

    def generate_label(self, content: str) -> str:
        return tag_label(self.tag_name, str(content))

    def match_label(self, tag: Tuple[str, str]) -> Optional[str]:
        if self.tag_name == tag[0]:
            return tag[1]
        return None

    def replace_tag_content(self, content: str) -> str:
        """将文本中的 ``[tag_name:xxx]`` 替换为 ``xxx``。"""
        needle_prefix = f"[{self.tag_name}:"
        if needle_prefix not in content:
            return content
        out: List[str] = []
        i = 0
        while i < len(content):
            start = content.find(needle_prefix, i)
            if start < 0:
                out.append(content[i:])
                break
            out.append(content[i:start])
            end = content.find("]", start)
            if end < 0:
                out.append(content[start:])
                break
            value = content[start + len(needle_prefix): end]
            out.append(value)
            i = end + 1
        return "".join(out)


def get_tag_desc() -> str:
    """返回 LLM 可见标签的描述拼接（排除纯工具路由标签）。"""
    return "".join(tag.get_tag_desc() for tag in tag_list if tag.visible_to_llm)


# ======================================================================
# 内置标签 — 消息上下文
# ======================================================================

# 上下文标签
time_tag = Tag(tag_name="time", tag_name_desc="对话时间")
uid_tag = Tag(tag_name="uid", tag_name_desc="用户 ID")
group_id_tag = Tag(tag_name="group_id", tag_name_desc="群组 ID")
name_tag = Tag(tag_name="name", tag_name_desc="用户名")
nickname_tag = Tag(tag_name="nickname", tag_name_desc="昵称")
channel_tag = Tag(tag_name="channel", tag_name_desc="来源频道标识")
platform_tag = Tag(tag_name="platform", tag_name_desc="来源平台（qq/telegram/web 等）")
message_id_tag = Tag(tag_name="message_id", tag_name_desc="当前消息 ID")
avatar_tag = Tag(tag_name="avatar", tag_name_desc="用户头像 URL")

# 媒体标签
media_file_tag = Tag(
    tag_name="media_file",
    tag_name_desc="媒体文件，格式 [media_file:类型:路径]，类型包括 image/voice/audio/video/file",
)

# 交互标签
at_uid_tag = Tag(tag_name="at_uid", tag_name_desc="消息中 @ 提及的用户 ID")
reply_to_tag = Tag(tag_name="reply_to", tag_name_desc="回复引用的消息 ID")
poke_tag = Tag(tag_name="poke", tag_name_desc="戳一戳事件的目标用户")
reaction_tag = Tag(tag_name="reaction", tag_name_desc="表情回应的 emoji ID")
forward_tag = Tag(tag_name="forward", tag_name_desc="转发消息的来源（原始发送者、频道名或消息 ID）")

# 富文本内容标签
json_card_tag = Tag(tag_name="json_card", tag_name_desc="JSON 卡片消息（QQ 分享链接、小程序卡片等），格式 [json_card:摘要文本]")

# 生成请求标签
tts_tag = Tag(tag_name="tts", tag_name_desc="文本转语音输出请求")
video_gen_tag = Tag(tag_name="video_gen", tag_name_desc="视频生成请求")

# ======================================================================
# 内置标签 — 工具路由
# ======================================================================

# 调度类标签
always_tag = Tag(tag_name="always", tag_name_desc="永驻工具，始终加载到上下文中", visible_to_llm=False)
core_tag = Tag(tag_name="core", tag_name_desc="核心工具，高优先级召回", visible_to_llm=False)
heartbeat_tag = Tag(tag_name="heartbeat", tag_name_desc="心跳任务工具", visible_to_llm=False)

# 功能域标签
planning_tag = Tag(tag_name="planning", tag_name_desc="目标规划与任务管理", visible_to_llm=False)
web_tag = Tag(tag_name="web", tag_name_desc="网络搜索与页面抓取", visible_to_llm=False)

# 发送能力标签
send_text_tag = Tag(tag_name="send_text", tag_name_desc="文本消息发送能力", visible_to_llm=False)
send_photo_tag = Tag(tag_name="send_photo", tag_name_desc="图片发送能力", visible_to_llm=False)
send_voice_tag = Tag(tag_name="send_voice", tag_name_desc="语音发送能力", visible_to_llm=False)
send_file_tag = Tag(tag_name="send_file", tag_name_desc="文件发送能力", visible_to_llm=False)

# 媒体处理子标签（PFC 从 [media_file:类型:路径] 解析后用于工具路由）
media_image_tag = Tag(tag_name="media:image", tag_name_desc="图片识别与处理", visible_to_llm=False)
media_video_tag = Tag(tag_name="media:video", tag_name_desc="视频处理", visible_to_llm=False)
media_voice_tag = Tag(tag_name="media:voice", tag_name_desc="语音转文字", visible_to_llm=False)
media_audio_tag = Tag(tag_name="media:audio", tag_name_desc="音频处理", visible_to_llm=False)
media_image_gen_tag = Tag(tag_name="media:image_gen", tag_name_desc="AI 图片生成", visible_to_llm=False)
media_image_edit_tag = Tag(tag_name="media:image_edit", tag_name_desc="AI 图片编辑", visible_to_llm=False)
media_file_route_tag = Tag(tag_name="media:file", tag_name_desc="文件读写操作", visible_to_llm=False)
