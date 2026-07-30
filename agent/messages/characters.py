from __future__ import annotations

from typing import Dict, Optional, Union

from pydantic import Field

from core.tags import Tag, group_id_tag, uid_tag

from .everything import CharType, EverythingGroup, MsgType, build_scope_id


class CharacterAgent(EverythingGroup):
    """
    角色（Assistant）的人设/系统提示词容器。\n
    personality：系统提示词列表，会转换为 OpenAI messages 结构缓存。
    """

    uid: Union[int, str] = 0
    group_id: Union[int, str] = 0
    char_type: CharType = CharType.ASSISTANT
    tag_list: list[Tag] = Field(default_factory=list)

    personality: list[str] = Field(default_factory=list)
    char_personality_cache: list[dict] = Field(default_factory=list)

    def model_post_init(self, __context) -> None:
        if self.personality and not self.char_personality_cache:
            for value in self.personality:
                self.char_personality_cache.append(
                    {MsgType.ROLE.value: CharType.SYSTEM.value, MsgType.CONTENT.value: value}
                )

    def get_personality_msg(self) -> list[dict]:
        if not self.char_personality_cache:
            self.model_post_init(None)
        return self.char_personality_cache


class EntityData(EverythingGroup):
    """
    人/群画像与计数器。\n
    说明：持久化后端将在 hybrid-storage 阶段改为 SQLite/Mongo 路由；
    当前先保持与旧逻辑相近的接口形态。
    """

    uid: Optional[Union[int, str]] = 0
    group_id: Union[int, str] = 0
    char_type: CharType = CharType.USER

    # 注意：这里先用普通 dict，后续会接入持久化适配层
    personality: Dict = Field(default_factory=dict)

    @property
    def identity_parts(self) -> tuple[str, str]:
        """实体身份键 (scope_type, scope_id)：用户实体（uid 非零）恒为 user 域。

        与 entity_scope（会话语义：群消息归群）刻意区分——身份语义用于画像/计数/
        内存实体键：同一个人无论在哪说话都是同一个 user 实体，不随群上下文漂移。
        """
        if self.uid not in (0, "0", None):
            return "user", build_scope_id(self.adapter_key, str(self.uid))
        return "group", build_scope_id(self.adapter_key, str(self.group_id))

    @property
    def identity_scope(self) -> str:
        """实体身份 entity_scope（``user_qq:123`` / ``group_qq:456``）。"""
        scope_type, scope_id = self.identity_parts
        return f"{scope_type}_{scope_id}"

    def add_conversations_num(self) -> int:
        self._plus_element("conv_num")
        self._plus_element("conv_update_num")
        return int(self._get_element("conv_update_num"))

    def get_update_conv_num(self) -> int:
        return int(self._get_element("conv_update_num"))

    def reset_conversations_num(self) -> None:
        """重置增量计数器，用于周期性画像更新后重新开始计数。"""
        self._set_element("conv_update_num", 0)

    def set_personality(self, content: str) -> None:
        self._set_element("personality", content)
        self._set_element("conv_update_num", 0)

    def get_personality(self) -> Dict:
        return self.personality

    def get_personality_desc(self) -> Optional[Dict]:
        if self.char_type and "personality" in self.personality:
            return {
                MsgType.ROLE.value: self.char_type.value,
                MsgType.CONTENT.value: self.personality["personality"],
            }
        return None

    def get_entity_desc(self) -> str:
        if self.uid in (0, "0", None):
            return group_id_tag.generate_label(str(self.group_id))
        return uid_tag.generate_label(str(self.uid))

    def _set_element(self, key: Union[int, str], default_value: Union[int, str] = 0) -> None:
        self.personality[str(key)] = default_value

    def _get_element(self, key: Union[int, str], default_value: Union[int, str] = 0):
        k = str(key)
        if k not in self.personality:
            self.personality[k] = default_value
        return self.personality[k]

    def _plus_element(self, key: Union[int, str], default_value: Union[int, str] = 0) -> None:
        k = str(key)
        if k not in self.personality:
            self.personality[k] = default_value
        self.personality[k] = int(self.personality[k]) + 1

