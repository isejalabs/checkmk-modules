import base64
from collections.abc import Iterator

from pydantic import BaseModel

from cmk.server_side_calls.v1 import HostConfig, Secret, SpecialAgentCommand, SpecialAgentConfig


class Kubernetes(BaseModel):
    api_server: str
    token: Secret
    ca_cert: str | None = None
    namespace: str = "longhorn-system"


class Params(BaseModel):
    endpoint: str | None = None
    kubernetes: Kubernetes | None = None


def _commands(params: Params, host_config: HostConfig) -> Iterator[SpecialAgentCommand]:
    if params.kubernetes is not None:
        kube = params.kubernetes
        # The token is a Password Store reference: Checkmk renders it as '<id>:<file>' and the agent resolves it
        # itself, so the token is never on the command line. The CA is public, but base64 keeps its line breaks off
        # the command line.
        arguments: list[str | Secret] = [
            "--api-server",
            kube.api_server,
            "--token",
            kube.token,
            "--namespace",
            kube.namespace,
        ]
        if kube.ca_cert and kube.ca_cert.strip():
            arguments += ["--ca-cert-b64", base64.b64encode(kube.ca_cert.strip().encode("ascii") + b"\n").decode()]
        yield SpecialAgentCommand(command_arguments=arguments)
        return
    if params.endpoint:
        yield SpecialAgentCommand(command_arguments=["--endpoint", params.endpoint])


special_agent_longhorn_capacity = SpecialAgentConfig(
    name="longhorn_capacity",
    parameter_parser=Params.model_validate,
    commands_function=_commands,
)
