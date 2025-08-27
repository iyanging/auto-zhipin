from typing import Literal, override

from openai import AsyncOpenAI
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.profiles import ModelProfile
from pydantic_ai.providers import Provider
from pydantic_ai.providers.deepseek import DeepSeekProvider
from pydantic_ai.providers.moonshotai import MoonshotAIProvider

LLMModel = Literal["deepseek-chat", "kimi-k2-0711-preview"]


def build_model(
    *,
    llm_model: LLMModel,
    llm_base_url: str | None,
    llm_api_key: str,
) -> Model:
    match llm_model:
        case "deepseek-chat":
            return OpenAIChatModel(
                llm_model,
                provider=CustomOpenAICompatProvider(
                    DeepSeekProvider(api_key=llm_api_key),
                    llm_base_url,
                ),
            )

        case "kimi-k2-0711-preview":
            return OpenAIChatModel(
                llm_model,
                provider=CustomOpenAICompatProvider(
                    MoonshotAIProvider(api_key=llm_api_key),
                    llm_base_url,
                ),
            )


class CustomOpenAICompatProvider(Provider[AsyncOpenAI]):
    original_provider: Provider[AsyncOpenAI]
    llm_base_url: str | None

    def __init__(
        self,
        original_provider: Provider[AsyncOpenAI],
        llm_base_url: str | None,
    ) -> None:
        super().__init__()

        self.original_provider = original_provider
        self.llm_base_url = llm_base_url

    @property
    @override
    def name(self) -> str:
        return self.original_provider.name

    @property
    @override
    def base_url(self) -> str:
        return self.llm_base_url or self.original_provider.base_url

    @property
    @override
    def client(self) -> AsyncOpenAI:
        return self.original_provider.client

    @override
    def model_profile(self, model_name: str) -> ModelProfile | None:
        return self.original_provider.model_profile(model_name)
