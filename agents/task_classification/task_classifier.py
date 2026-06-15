"""
任务分类器 - 专门负责判断用户请求的类型

职责：
1. 接收用户输入，分析其意图
2. 根据预定义的分类规则，将任务归类为：
   - appointment（预约任务）
   - query（查询任务）  
   - pay（支付任务）
   - statistics（统计任务）
   - other（其他任务）
3. 提供清晰的分类结果和置信度
"""

from langchain.prompts import PromptTemplate
from langchain_core.language_models.chat_models import BaseChatModel
from typing import Dict, Any


class TaskClassifier:
    """任务分类器 - 使用LLM进行智能任务分类"""
    
    def __init__(self, llm: BaseChatModel):
        self.llm = llm
        self._initialize_prompt()
        self.chain = self.prompt | self.llm
    
    def _initialize_prompt(self):
        """初始化分类提示词模板"""
        self.prompt = PromptTemplate(
            input_variables=["task"],
            template=(
                "你是一个服务预约系统的助手，你会处理来自用户和工作人员的消息，你的任务是对本次任务进行分类。\n"
                "用户可能会咨询服务价格、有哪些工作人员、各自的特点等，这类任务归类为查询任务。\n"
                "用户可能会请求预约，比如'请帮我预约今天下午3点的服务1小时'，这类任务归类为预约任务。\n"
                "appointment机器人也可能发来任务，告知用户选择了某位工作人员做某个项目，这类任务归类为支付任务。\n"
                "工作人员可能会发来任务，比如告知某个用户需要延长服务时间，这类任务归类为预约任务。\n"
                "工作人员也可能告知已完成当前任务，这类任务归类为统计任务。\n"
                "如果输入的任务与上述都无关，请归类为其它任务。\n"
                "请将以下任务归类为以下类别，输出只能选择以下之一：\n"
                "1. appointment（预约任务）\n"
                "2. query（查询任务）\n"
                "3. pay（支付任务）\n"
                "4. statistics（统计任务）\n"
                "5. other（其它任务）\n"
                "只返回类别英文名。\n\n"
                "举例说明：假如task为'我要预约8号工作人员1小时的推拿'，则输出appointment。\n"
                "假如输入为我想问一下按摩房在哪里，则输入query。\n"
                "以下是本次归类任务:\n"
                "任务内容：{task}"
            )
        )
    
    async def classify_task(self, task: str) -> str:
        """
        分类任务
        
        Args:
            task: 用户输入的任务内容
            
        Returns:
            str: 分类结果 ('appointment', 'query', 'pay', 'statistics', 'other')
        """
        try:
            category_msg = await self.chain.ainvoke({"task": task})
            category = category_msg.content.strip().lower()
            
            # 验证分类结果是否有效
            valid_categories = {'appointment', 'query', 'pay', 'statistics', 'other'}
            if category not in valid_categories:
                return 'other'  # 默认归类为其他
                
            return category
            
        except Exception as e:
            print(f"任务分类失败: {str(e)}")
            return 'other'  # 发生错误时默认归类为其他
    
    def get_category_description(self, category: str) -> str:
        """获取分类类别的描述信息"""
        descriptions = {
            'appointment': '预约任务 - 用户或工作人员的预约相关请求',
            'query': '查询任务 - 用户咨询服务信息、价格、工作人员等',
            'pay': '支付任务 - 完成预约后的支付相关事务',
            'statistics': '统计任务 - 工作人员上报工作完成状态',
            'other': '其他任务 - 与按摩服务无关的请求'
        }
        return descriptions.get(category, '未知任务类型')
