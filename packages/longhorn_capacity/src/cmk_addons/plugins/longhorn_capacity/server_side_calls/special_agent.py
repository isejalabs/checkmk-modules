from collections.abc import Iterator

from pydantic import BaseModel

from cmk.server_side_calls.v1 import HostConfig, SpecialAgentCommand, SpecialAgentConfig


class Params(BaseModel):
    endpoint: str


def _commands(params: Params, host_config: HostConfig) -> Iterator[SpecialAgentCommand]:
    yield SpecialAgentCommand(command_arguments=["--endpoint", params.endpoint])


special_agent_longhorn_capacity = SpecialAgentConfig(
    name="longhorn_capacity",
    parameter_parser=Params.model_validate,
    commands_function=_commands,
)
