from collections.abc import Iterator

from pydantic import BaseModel

from cmk.server_side_calls.v1 import HostConfig, Secret, SpecialAgentCommand, SpecialAgentConfig


class Params(BaseModel):
    endpoint: str
    access_key: str
    secret_key: Secret
    buckets: list[str]


def _commands(params: Params, host_config: HostConfig) -> Iterator[SpecialAgentCommand]:
    # The secret is a Password Store reference: Checkmk renders it as '<id>:<file>' and the agent resolves it itself,
    # so the secret is never on the command line.
    yield SpecialAgentCommand(
        command_arguments=[
            "--endpoint",
            params.endpoint,
            "--access-key",
            params.access_key,
            "--secret-key",
            params.secret_key,
            "--buckets",
            ",".join(params.buckets),
        ]
    )


special_agent_rustfs_quota = SpecialAgentConfig(
    name="rustfs_quota",
    parameter_parser=Params.model_validate,
    commands_function=_commands,
)
