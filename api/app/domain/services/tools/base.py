import inspect
from collections.abc import Callable
from typing import Any

from app.domain.models.tool_policy import (
    CONSERVATIVE_TOOL_POLICY,
    ToolDescriptor,
    ToolExecutionPolicy,
)
from app.domain.models.tool_result import ToolResult, normalize_tool_result
from app.domain.services.tools.capability_policy import (
    CapabilityDeniedError,
    CapabilityPolicy,
)

"""
OpenCitadel 工具设计思路:
1.所有工具都必须继承一个BaseTool基类，拥有统一的invoke方法用于调用该类下的对应工具;
2.定义一个装饰器，被该装饰器装饰的方法会填充_tool_name、_tool_description、_tool_schema属性;
3.工具类可以通过get_tools快速获取基于缓存的schema参数信息，这样LLM就可以便捷调用;
4.LLM生成的内容有可能会有幻觉，在调用工具前需要筛选出LLM生成参数中符合工具的相关数据;
"""


def tool(
    name: str,
    description: str,
    parameters: dict[str, dict[str, Any]],
    required: list[str],
    policy: ToolExecutionPolicy = CONSERVATIVE_TOOL_POLICY,
) -> Callable:
    """定义OpenAI工具装饰器，用于将一个函数/方法添加上对应的工具声明"""

    def decorator(func):
        """装饰器函数，用于将name/description/parameters/required转换成对应的属性"""
        # 1.创建工具声明数据结构
        tool_schema = {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": parameters,
                    "required": required,
                },
            },
        }

        # 2.将对应属性绑定到func上
        func._tool_name = name
        func._tool_description = description
        func._tool_schema = tool_schema
        func._tool_policy = policy

        return func

    return decorator


class BaseTool:
    """基础工具类，用于定义一个工具类，管理统一的工具集"""

    name: str = ""  # 工具集的名字

    def __init__(self) -> None:
        """构造函数，完成缓存初始化"""
        self._tools_cache = None
        self._tool_methods_cache = None
        self._capability_policy: CapabilityPolicy | None = None

    def _get_tool_methods(self) -> dict[str, Callable]:
        if self._tool_methods_cache is not None:
            return self._tool_methods_cache
        self._tool_methods_cache = {
            method._tool_name: method
            for _, method in inspect.getmembers(self, inspect.ismethod)
            if hasattr(method, "_tool_name")
        }
        return self._tool_methods_cache

    @classmethod
    def _filter_parameters(cls, method: Callable, kwargs: dict[str, Any]) -> dict[str, Any]:
        """传递method+kwargs并过滤参数，使其符合method参数的要求，因为LLM输出的内容有可能有幻觉"""
        sign = inspect.signature(method)
        return {key: value for key, value in kwargs.items() if key in sign.parameters}

    def get_tools(self) -> list[dict[str, Any]]:
        """获取所有已注册的工具列表schema信息，用于LLM绑定工具"""
        # 1.判断缓存是否存在
        if self._tools_cache is not None:
            return self._tools_cache

        tools = [descriptor.schema for descriptor in self.get_tool_descriptors()]

        # 4.创建缓存后返回
        self._tools_cache = tools
        return tools

    def has_tool(self, tool_name: str) -> bool:
        """传递工具名字，判断该工具集下是否存在该工具"""
        return tool_name in self._get_tool_methods()

    def get_tool_descriptor(self, name: str) -> ToolDescriptor:
        """Return the executable method and governance metadata for a registered tool."""
        method = self._get_tool_methods().get(name)
        if method is None:
            raise ValueError(f"工具[{name}]未找到")
        return ToolDescriptor(
            name=name,
            schema=method._tool_schema,
            method=method,
            tool_pack=self.name,
            policy=getattr(method, "_tool_policy", CONSERVATIVE_TOOL_POLICY),
        )

    def get_tool_descriptors(self) -> list[ToolDescriptor]:
        """Return descriptors visible under the active session policy."""
        descriptors = [self.get_tool_descriptor(name) for name in self._get_tool_methods()]
        if self._capability_policy is None:
            return descriptors
        return [
            descriptor
            for descriptor in descriptors
            if self._capability_policy.allows(
                descriptor.policy,
                tool_name=descriptor.name,
            )
        ]

    async def invoke(self, tool_name: str, **kwargs) -> ToolResult:
        """根据传递的工具名+kwargs调用指定工具并获取结果"""
        descriptor = self.get_tool_descriptor(tool_name)
        if self._capability_policy is not None and not self._capability_policy.allows(
            descriptor.policy,
            tool_name=descriptor.name,
        ):
            raise CapabilityDeniedError(
                f"当前会话策略禁止工具[{tool_name}]",
                layer="execution",
                tool_name=tool_name,
            )
        # 1.循环遍历工具集的所有方法
        method = self._get_tool_methods().get(tool_name)
        if method is not None:
            # 2.筛选传递的kwargs参数保留method对应的参数，多余的剔除
            filtered_kwargs = self._filter_parameters(method, kwargs)
            raw = await method(**filtered_kwargs)
            return normalize_tool_result(raw)

        # 3.如果没有找到工具则抛出错误
        raise ValueError(f"工具[{tool_name}]未找到")


class PolicyBoundTool(BaseTool):
    """Per-runner policy view over a shared tool pack without mutating it."""

    def __init__(self, wrapped: BaseTool, policy: CapabilityPolicy) -> None:
        super().__init__()
        self._wrapped = wrapped
        self._capability_policy = policy
        self.name = wrapped.name

    def get_tool_descriptor(self, name: str) -> ToolDescriptor:
        return self._wrapped.get_tool_descriptor(name)

    def _allows(self, descriptor: ToolDescriptor) -> bool:
        if descriptor.tool_pack in {"mcp", "a2a"}:
            return self._capability_policy.allows_integration(
                descriptor.policy,
                tool_name=descriptor.name,
            )
        return self._capability_policy.allows(
            descriptor.policy,
            tool_name=descriptor.name,
        )

    def get_tool_descriptors(self) -> list[ToolDescriptor]:
        return [
            descriptor
            for descriptor in self._wrapped.get_tool_descriptors()
            if self._allows(descriptor)
        ]

    def get_tools(self) -> list[dict[str, Any]]:
        return [descriptor.schema for descriptor in self.get_tool_descriptors()]

    def has_tool(self, tool_name: str) -> bool:
        return any(descriptor.name == tool_name for descriptor in self.get_tool_descriptors())

    async def invoke(self, tool_name: str, **kwargs) -> ToolResult:
        descriptor = self._wrapped.get_tool_descriptor(tool_name)
        if not self._allows(descriptor):
            raise CapabilityDeniedError(
                f"当前会话策略禁止工具[{tool_name}]",
                layer="execution",
                tool_name=tool_name,
            )
        return await self._wrapped.invoke(tool_name, **kwargs)

    def __getattr__(self, name: str):
        return getattr(self._wrapped, name)
