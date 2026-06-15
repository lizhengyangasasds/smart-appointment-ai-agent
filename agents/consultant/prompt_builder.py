"""
提示词构建器

负责构建各种类型的提示词
"""

from typing import List, Dict, Any


class PromptBuilder:
    """提示词构建器"""
    
    def __init__(self):
        self.system_prompt = self._create_system_prompt()
        self.classification_prompt_template = self._create_classification_prompt_template()
    
    def _create_system_prompt(self) -> str:
        """创建系统提示词"""
        return (
            "你是一个推拿房的前台接待员，负责为客户解答关于推拿服务、预约、价格、营业时间、地址、交通等相关问题。"
            "我会为你提供相关的知识库信息，请基于这些信息来回答用户的问题。"
            "如果知识库中没有相关信息，请提供合理的兜底回答，比如："
            "- 对于地址问题：抱歉，具体地址信息请您致电我们店里咨询，我们会详细为您指路。"
            "- 对于交通问题：建议您可以使用地图导航，或者致电我们获取详细的交通指引。"
            "- 对于其他缺失信息：请您直接致电我们或到店咨询，我们会为您提供更详细的信息。"
            "请用专业、礼貌、简洁的语言回复用户。"
            "如果用户的问题与推拿房服务完全无关（如天气、股票、新闻等），请礼貌地告知用户你只能回答推拿相关问题。"
            "回答时要自然流畅，不要明显地表现出是在查阅资料。"
        )
    
    def _create_classification_prompt_template(self) -> str:
        """创建分类提示词模板"""
        return (
            "你是一个分类器，判断用户输入是否是关于推拿店的咨询类问题。\n"
            "咨询类问题包括：推拿服务、价格、营业时间、服务项目、技师情况、店铺地址、交通路线、联系方式、店铺环境等。\n"
            "非咨询类问题包括：预约服务（我要预约、帮我安排等）、取消预约、天气、股票、新闻等完全无关的话题。\n"
            "如果是咨询类问题，回答'YES'。如果是预约类问题或完全无关问题，回答'NO'。\n"
            "只回答YES或NO。\n\n"
            "用户输入：{user_input}"
        )
    
    def build_consultation_prompt(self, user_input: str, knowledge_docs: List[Dict[str, Any]]) -> str:
        """构建咨询提示词"""
        context = self._build_knowledge_context(knowledge_docs)
        return f"{self.system_prompt}\n\n{context}\n用户问题：{user_input}\n\n请回答用户的问题。"
    
    def build_classification_prompt(self, user_input: str) -> str:
        """构建分类提示词"""
        return self.classification_prompt_template.format(user_input=user_input)
    
    def _build_knowledge_context(self, knowledge_docs: List[Dict[str, Any]]) -> str:
        """构建知识库上下文"""
        if not knowledge_docs:
            return "没有找到直接相关的知识库信息，请基于你对推拿服务的专业知识回答。"
        
        context = "\n以下是相关的知识库信息：\n"
        for i, doc in enumerate(knowledge_docs, 1):
            context += f"{i}. {doc['content']}\n"
        context += "\n请基于以上信息回答用户问题。如果知识库信息不足以回答问题，请基于你对推拿服务的一般了解来补充回答。\n"
        
        return context
