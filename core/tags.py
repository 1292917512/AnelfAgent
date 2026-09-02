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
    seen: set[Tuple[str, str]] = set()
    result: List[Tuple[str, str]] = []
    for tag in extract_tag_brackets(text):
        try:
            tag_tuple = etag(tag)
        except ValueError:
            continue
        if tag_tuple not in seen:
            seen.add(tag_tuple)
            result.append(tag_tuple)
    return result


def batch_remove_tags(text: str) -> str:
    """批量移除所有标签（将 ``[k:v]`` 替换成 ``v``，保留值部分）。

    键限制为单词字符（与 tag_label 生成的键一致），值不允许跨 ``[``、``]``
    与换行：避免多行文本中首个 ``[`` 与远处的 ``:``、``]`` 错误配对，
    吞掉大段正文（如执行摘要中的 MAC 地址、JSON 数组）。
    """
    return re.sub(r"\[(?:\w+):([^\[\]\n]*)\]", r"\1", text)


# 消息上下文元数据标签（渲染进对话历史、仅作系统元数据，禁止出现在出站文本中）
_META_TAG_NAMES = (
    "time", "channel", "session_id", "message_id",
    "group_id", "uid", "name", "nickname", "reply_to", "to_me", "push",
)
_meta_tag_pattern = re.compile(r"\[(?:" + "|".join(_META_TAG_NAMES) + r"):[^\]]*\]")


def strip_message_meta_tags(text: str) -> str:
    """移除文本中的消息元数据标签（整段删除，不保留值）。

    用于出站文本清洗：LLM 可能模仿对话历史中的标签格式，
    将 [message_id:xxx] 等元数据带进回复内容，发送前需剥离。
    功能性标签（如 [at_uid:xxx]）不在移除范围。
    """
    return _meta_tag_pattern.sub("", text)


# 功能性标签（媒体/交互/生成请求）：富媒体频道由独立结构携带，纯文本界面整段剥离
_FUNC_TAG_NAMES = (
    "media_file", "media_type", "media_path", "media_file_id",
    "json_card", "tts", "video_gen", "at_uid", "poke", "reaction", "forward",
)
_func_tag_pattern = re.compile(r"\[(?:" + "|".join(_FUNC_TAG_NAMES) + r"):[^\]]*\]")


def strip_functional_tags(text: str) -> str:
    """移除文本中的功能性标签（整段删除，不保留值）。

    用于 webui 等纯文本界面的出站/历史清洗：媒体内容由独立结构
    （media 帧 / 附件字段）携带，正文中的标签语法不渲染。
    """
    return _func_tag_pattern.sub("", text)


DEFAULT_TIME_FORMAT = "%Y年%m月%d日%H时%M分%S秒"


def get_current_time(time_format: str = DEFAULT_TIME_FORMAT) -> str:
    """返回当前时间的格式化字符串。"""
    return datetime.datetime.now().strftime(time_format)


def format_time(ts_ns: int, time_format: str = DEFAULT_TIME_FORMAT) -> str:
    """将纳秒时间戳格式化为时间字符串。"""
    return datetime.datetime.fromtimestamp(ts_ns / 1_000_000_000).strftime(time_format)


def get_time_tag(ts_ns: Optional[int] = None) -> str:
    """返回时间标签 ``[time:...]``；给定纳秒时间戳时按该时刻格式化，否则取当前时间。"""
    if ts_ns is None:
        return tag_label("time", get_current_time())
    return tag_label("time", format_time(ts_ns))


def rm_unless_text(text: str) -> str:
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
        # 按 tag_name 去重：重复定义时替换旧条目，避免 tag_list 膨胀与描述重复拼接
        for i, existing in enumerate(tag_list):
            if existing.tag_name == self.tag_name:
                tag_list[i] = self
                return
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
session_id_tag = Tag(tag_name="session_id", tag_name_desc="会话 ID（同一频道会话上下文标识）")
platform_tag = Tag(tag_name="platform", tag_name_desc="来源平台（qq/telegram/web 等）")
message_id_tag = Tag(tag_name="message_id", tag_name_desc="当前消息 ID")
to_me_tag = Tag(
    tag_name="to_me",
    tag_name_desc="本条群消息 @ 了你、是直接对你说的话；"
                  "群聊历史中没有此标签的消息是群员之间的对话，不是发给你的请求，"
                  "无需回应，也不要当作对你的提问、托付或欠下的待办（私聊消息默认都是对你说，无需此标签）",
)
avatar_tag = Tag(tag_name="avatar", tag_name_desc="用户头像 URL")
kind_tag = Tag(
    tag_name="kind",
    tag_name_desc="消息类别：notification 表示平台自动推送的通知（如回复/点赞提醒，"
                  "不是对方主动对你发起的对话，要回应请用对应频道的工具操作，不要当作聊天消息直接回复）；"
                  "event 表示场景事件（如直播间礼物/醒目留言）；system 表示系统消息；"
                  "无此标签的消息是真人聊天",
)

# 媒体标签
media_file_tag = Tag(
    tag_name="media_file",
    tag_name_desc="媒体文件，格式 [media_file:类型:路径]，类型包括 image/voice/audio/video/file",
)
media_type_tag = Tag(
    tag_name="media_type",
    tag_name_desc="媒体类型（image/voice/audio/video/file），与 media_path 成对出现于消息尾部",
)
media_path_tag = Tag(
    tag_name="media_path",
    tag_name_desc="媒体文件的本地路径或 URL；为「未下载」时表示文件未落地，"
                  "可配合 media_file_id 用 qq_download_file 或直接用 URL 调 web_download 按需下载",
)
media_file_id_tag = Tag(
    tag_name="media_file_id",
    tag_name_desc="平台文件 ID（如 QQ 文件），可传给 qq_download_file 按需下载到本地后再分析",
)

# 交互标签
at_uid_tag = Tag(tag_name="at_uid", tag_name_desc="消息中 @ 提及的用户 ID")
reply_to_tag = Tag(tag_name="reply_to", tag_name_desc="回复引用的消息 ID")
poke_tag = Tag(tag_name="poke", tag_name_desc="戳一戳事件的目标用户")
reaction_tag = Tag(tag_name="reaction", tag_name_desc="表情回应的 emoji ID")
forward_tag = Tag(tag_name="forward", tag_name_desc="转发消息的来源（原始发送者、频道名或消息 ID）")

# 富文本内容标签
json_card_tag = Tag(tag_name="json_card", tag_name_desc="JSON 卡片消息（QQ 分享链接、小程序卡片等），格式 [json_card:摘要文本]")

# 推送标签（实体主动推送给 AI 的系统通知，区别于用户消息）
push_tag = Tag(
    tag_name="push",
    tag_name_desc="实体推送的系统通知（非用户消息），格式 [push:来源实体]，"
                  "如同手机弹窗；可按需响应或忽略，回复用户前自行判断是否需要提及",
)

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
