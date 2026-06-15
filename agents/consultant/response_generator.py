"""
响应生成器

负责生成AI响应内容
"""

from typing import Dict, Any, AsyncGenerator
from langchain_core.language_models.chat_models import BaseChatModel
from .prompt_builder import PromptBuilder


class ResponseGenerator:
    """响应生成器"""
    
    def __init__(self, llm: BaseChatModel):
        self.llm = llm
        self.prompt_builder = PromptBuilder()
    
    async def generate_response(self, user_input: str, knowledge_docs: list) -> str:
        """生成标准响应"""
        try:
            prompt = self.prompt_builder.build_consultation_prompt(user_input, knowledge_docs)
            response = await self.llm.ainvoke([{"role": "user", "content": prompt}])
            return response.content
        except Exception as e:
            return f"抱歉，处理您的问题时出现了错误。请稍后再试。"
    
    async def generate_response_stream(self, user_input: str, knowledge_docs: list) -> AsyncGenerator[str, None]:
        """生成流式响应"""
        try:
            prompt = self.prompt_builder.build_consultation_prompt(user_input, knowledge_docs)
            response = await self.llm.ainvoke([{"role": "user", "content": prompt}])
            content = response.content
            
            # 只在开头添加一次REPLY标签，然后逐字符输出
            yield "[REPLY][咨询机器人]"
            for char in content:
                yield char
                
        except Exception as e:
            error_msg = f"抱歉，处理您的问题时出现了错误：{str(e)}"
            yield "[REPLY][咨询机器人]"
            for char in error_msg:
                yield char
    
    def create_unrelated_message(self) -> str:
        """创建与咨询无关的回复消息"""
        return "[THOUGHT][咨询机器人] 咨询机器人：这个问题不是咨询类问题，我将转回给归类机器人处理。"
