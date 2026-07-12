"""
用户输入解析器

负责解析用户输入并提取预约相关信息
"""

import json
import logging
from typing import Dict, Any, Generator, List, Optional
from langchain.prompts import PromptTemplate
from langchain_core.chat_history import InMemoryChatMessageHistory
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, AIMessage

logger = logging.getLogger(__name__)


def _format_bad_cases_for_prompt(bad_cases: Optional[List[Dict[str, Any]]], limit: int = 5) -> str:
    """把反思系统产出的坏案例集合压缩成可注入 LLM prompt 的一段说明。

    这里的契约由 reflection_repository.ReflectionLog.bad_cases 决定：
        {description, category, task_type, trigger, suggested_fix, ...}

    设计原则：
    1. 严格控制 token 数（每条只截 description + suggested_fix，避免 prompt 膨胀）
    2. 只保留与预约链路直接相关的描述，避免污染输入解析
    3. 返回空字符串时上游不拼入 prompt，对无反思数据的早期阶段零成本
    """
    if not bad_cases:
        return ""

    lines: List[str] = []
    for bc in bad_cases[:limit]:
        desc = (bc.get("description") or "").strip()
        fix = bc.get("suggested_fix") or {}
        fix_text = ""
        if isinstance(fix, dict):
            fix_text = (fix.get("note") or fix.get("action") or "").strip()
        elif isinstance(fix, str):
            fix_text = fix.strip()

        if desc:
            lines.append(f"- {desc}")
        if fix_text:
            lines.append(f"  规避建议: {fix_text}")

    if not lines:
        return ""

    body = "\n".join(lines)
    return (
        "\n【已知坏案例（来自反思系统，请避免重复犯）】\n"
        f"{body}\n"
        "解析用户输入时如果看到与上述坏案例相似的场景，请优先按规避建议处理（例如更谨慎地拆分姓名/偏好）。\n"
    )


class InputParser:
    """用户输入解析器"""

    def __init__(self, llm: BaseChatModel, reflection_bad_cases: Optional[List[Dict[str, Any]]] = None):
        self.llm = llm
        # 闭环 1：把反思系统产出的坏案例注入到解析 prompt，让 LLM 主动避开已知失败模式
        self._reflection_bad_cases = reflection_bad_cases or []
        self.prompt = self._create_prompt_template()
        self.chain = self.prompt | self.llm

    def update_reflection_bad_cases(self, bad_cases: Optional[List[Dict[str, Any]]]) -> None:
        """在 AppointmentAgent 已实例化后异步注入坏案例（避免重启解析器）。

        直接重建 prompt，让下一次 parse_stream 立刻生效；
        chain 仅在 llm 已绑定时重建，避免单测时 llm=None 抛异常。
        """
        self._reflection_bad_cases = bad_cases or []
        self.prompt = self._create_prompt_template()
        if self.llm is not None:
            self.chain = self.prompt | self.llm

    def _create_prompt_template(self) -> PromptTemplate:
        """创建预约信息提取的Prompt模板"""
        from config.time_config import time_config
        current_date = time_config.current_date_str()
        current_datetime = time_config.current_datetime_str()

        # 闭环 1：把坏案例拼入 prompt；如果当前没有反思数据则完全不注入（保持原行为）
        bad_cases_block = _format_bad_cases_for_prompt(self._reflection_bad_cases)

        return PromptTemplate(
            input_variables=["history", "user_input"],
            template=(
                "你是一个预约机器人，负责帮用户预约服务。\n"
                f"当前日期是{current_date}，当前北京时间是{current_datetime}。\n"
                "当前已知信息：{history}\n"
                "用户输入：{user_input}\n"
                "特别注意：如果用户输入是对推荐技师确认问题的回应（如\"是\"、\"好\"、\"可以\"、\"不\"、\"不要\"等简短回复），请优先识别为confirmation，而不要标记为unrelated。\n"
                "重要：请你只输出纯JSON格式，不要添加任何markdown标记如```json或```，不要添加任何其他文字说明，直接输出JSON：\n"
                "{{\n"
                '  "gender": "技师性别（如男/女/未知）",\n'
                '  "start_time": "预约起始时间，必须转换为标准格式YYYY-MM-DD HH:MM。如果用户说今天下午3点，转换为当前日期 15:00；如果说明天上午10点，转换为明天日期 10:00。如果只说时间没说日期，默认为今天。如果完全没有时间信息则为未知",\n'
                '  "duration": "服务时长，统一转换为分钟数格式，如180分钟、60分钟。如果没有明确时长则为未知",\n'
                '  "project": "服务项目（如按摩/未知）",\n'
                '  "preference": "用户倾向（如力气大/力气小/无）",\n'
                '  "technician_name": "指定技师姓名（如果用户明确提到技师名字，如张伟、李小美等，否则为未知）",\n'
                '  "confirmation": "如果用户在回应技师推荐的确认问题，提取用户的回复内容（如是/好/可以/不/不要等），否则为未知",\n'
                '  "info_complete": "根据实际情况判断：1)如果指定了技师名且不为未知，需要start_time、project、duration都不为未知；2)如果没指定技师名，需要start_time、project、duration、gender都不为未知",\n'
                '  "unrelated": "如果用户的问题和预约无关（如问天气、聊天等），则为true，否则为false。注意：对推荐技师的确认回复（是/不等）不应标记为unrelated",\n'
                '  "missing_info": "如果info_complete为false，请列出缺少的关键信息，如[start_time, project]等"\n'
                "}}\n"
                "判断逻辑：\n"
                "1. 如果用户明确指定了技师姓名（如\"张伟技师\"、\"预约李小美\"、\"帮我约张伟\"等），请务必提取technician_name。\n"
                "   特别注意：技师名通常为2~4个汉字的真实姓名（如张伟、王强、李娜、赵敏等）。\n"
                "   【关键规则】technician_name 必须是真实的人名（2~4个汉字的姓名），绝对不能是以下内容：\n"
                "     - 服务项目名（如\"按摩\"、\"按摩服务\"、\"推拿\"、\"足疗\"、\"spa\"等）\n"
                "     - 描述性短语（如\"手劲大的女技师\"、\"力气大的男老师\"、\"经验丰富的\"、\"手法好的\"等）\n"
                "     - 带有修饰词的组合（如\"女按摩师\"、\"男技师\"、\"高级技师\"等）\n"
                "     - 任何不是真实人名的词汇\n"
                "   如果无法确定是真实的人名，请将 technician_name 设为\"未知\"，并把这些描述分别提取到 gender / preference / project 字段。\n"
                "   例如：用户说\"我要预约按摩服务\"，这是服务项目，应放入project=\"按摩\"，technician_name=\"未知\"。\n"
                "   例如：用户说\"手劲大的女技师\"，应放入gender=\"女\"，preference=\"手劲大\"，technician_name=\"未知\"。\n"
                "2. 如果用户在回应推荐技师的确认问题（如回复\"是\"、\"好\"、\"可以\"、\"不\"、\"不要\"等），请提取到confirmation字段，并且不要将其标记为unrelated\n"
                "3. 必需信息判断：\n"
                "   - 如果指定了技师名：需要start_time、project、duration\n"
                "   - 如果没指定技师名：需要start_time、project、duration、gender\n"
                "3. 只有当所有必需信息都不是'未知'时，info_complete才为true\n"
                "4. 如果用户的问题和预约无关，请将unrelated设为true\n"
                "5. 描述性偏好（如'手劲大'、'手法细腻'、'力气小'、'擅长经络'等）应放入preference字段。\n"
                "【再次强调】project 字段应提取服务项目类型（如\"按摩\"），technician_name 必须是真实人名，绝不能把项目名误识别为技师名！\n"
                "再次强调：只输出纯JSON，不要有任何代码块标记或其他文字。"
                f"{bad_cases_block}"
            )
        )
    
    def parse_stream(
        self,
        user_input: str,
        chat_history: InMemoryChatMessageHistory,
        memory_context: str = "",
    ) -> Generator[str, None, str]:
        """流式解析用户输入

        Args:
            user_input: 用户输入
            chat_history: LangChain InMemoryChatMessageHistory（内部短期记忆）
            memory_context: 外部记忆上下文（对话历史摘要+用户画像，来自 MemoryManager）
        """
        # 添加用户消息到历史
        chat_history.add_message(HumanMessage(content=user_input))

        # 构建历史字符串
        history_str = "\n".join(
            [f"用户：{m.content}" if m.type == "human" else f"机器人：{m.content}"
             for m in chat_history.messages]
        )

        # 拼接外部记忆上下文
        if memory_context:
            history_str = f"【对话历史摘要】：\n{memory_context}\n\n【当前对话】：\n{history_str}"

        # 流式调用LLM
        response_stream = self.chain.stream({"history": history_str, "user_input": user_input})
        ai_content = ""

        for chunk in response_stream:
            token = chunk.content if hasattr(chunk, "content") else str(chunk)
            ai_content += token
            yield token

        # 添加AI回复到历史
        chat_history.add_message(AIMessage(content=ai_content))
        return ai_content
    
    def parse_data(self, ai_content: str) -> Dict[str, Any]:
        """解析AI返回的JSON数据"""
        try:
            data = json.loads(ai_content)
        except json.JSONDecodeError:
            return {
                "gender": "未知",
                "start_time": "未知",
                "duration": "未知",
                "project": "未知",
                "preference": "未知",
                "technician_name": "未知",
                "confirmation": "未知",
                "info_complete": False,
                "unrelated": False,
                "missing_info": ["所有信息"]
            }

        # 兜底：校验 technician_name 是否为真实姓名
        # 避免 LLM 把服务项目名（如"按摩服务"）或描述性短语误识别为技师名
        tech_name = data.get("technician_name")
        if tech_name and tech_name != "未知" and not self._looks_like_real_name(tech_name):
            print(f"[WARN] technician_name '{tech_name}' 不符合真实姓名规则，重置为未知")
            # 如果误识别为技师名的内容里包含服务项目关键词，迁移到 project 字段
            project_keywords = ["按摩", "推拿", "足疗", "spa", "理疗", "养生", "经络", "刮痧", "拔罐"]
            if any(kw in tech_name for kw in project_keywords):
                if not data.get("project") or data.get("project") == "未知":
                    # 抽取项目名（去掉"服务"等后缀）
                    for kw in project_keywords:
                        if kw in tech_name:
                            data["project"] = kw
                            break
            data["technician_name"] = "未知"

        # 兜底：校验 project 是否合理
        project = data.get("project")
        if project and project != "未知" and not self._looks_like_valid_project(project):
            print(f"[WARN] project '{project}' 不合理，重置为未知")
            data["project"] = "未知"

        # 重新计算 info_complete
        required_fields = ["start_time", "project", "duration"]
        # 如果指定了真实姓名，则不需要性别
        real_name_provided = bool(
            data.get("technician_name") and data["technician_name"] != "未知"
        )
        if not real_name_provided:
            required_fields.append("gender")

        info_complete = all(
            data.get(f) and data[f] != "未知"
            for f in required_fields
        )
        data["info_complete"] = info_complete

        if not info_complete:
            data["missing_info"] = [
                f for f in required_fields
                if not data.get(f) or data.get(f) == "未知"
            ]
        else:
            data["missing_info"] = []

        return data

    @staticmethod
    def _looks_like_real_name(name: str) -> bool:
        """
        判断是否为真实姓名（2~4个汉字）。
        排除服务项目、描述性短语等。
        """
        if not name or not isinstance(name, str):
            return False
        name = name.strip()
        # 长度必须是 2~4 个汉字
        if not (2 <= len(name) <= 4):
            return False
        # 必须全部是汉字
        if not all('\u4e00' <= ch <= '\u9fff' for ch in name):
            return False
        # 排除常见服务项目关键词（即使作为人名也不应该）
        invalid_keywords = [
            "按摩", "推拿", "足疗", "spa", "理疗", "养生",
            "经络", "刮痧", "拔罐", "服务", "技师", "老师",
            "手劲", "手劲大", "手劲小", "力气", "力气大", "力气小",
            "手法", "经验", "丰富", "高级", "中级", "初级",
            "好的", "好", "不错", "推荐", "最好", "最好",
        ]
        for kw in invalid_keywords:
            if kw in name:
                return False
        # 排除带"大/小/好"的描述性短语（4字描述）
        # 例如 "手劲大的", "手法好的", "经验丰富的"
        if len(name) == 4:
            if name[2:] in ["大的", "小的", "好的", "的", "经验丰富"]:
                return False
        return True

    @staticmethod
    def _looks_like_valid_project(project: str) -> bool:
        """判断是否为合理的服务项目"""
        if not project or not isinstance(project, str):
            return False
        project = project.strip()
        if len(project) > 10:
            return False
        # 排除带"服务"后缀的描述（如"按摩服务"、"足疗服务"等）
        if "服务" in project or "技师" in project or "老师" in project:
            return False
        valid_keywords = [
            "按摩", "推拿", "足疗", "spa", "理疗", "养生",
            "经络", "刮痧", "拔罐", "肩颈", "腰背", "头部",
            "全身", "局部", "中式", "泰式", "精油"
        ]
        return any(kw in project for kw in valid_keywords)
