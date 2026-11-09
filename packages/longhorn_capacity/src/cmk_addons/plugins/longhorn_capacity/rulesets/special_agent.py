from cmk.rulesets.v1 import Help, Title
from cmk.rulesets.v1.form_specs import (
    DefaultValue,
    DictElement,
    Dictionary,
    MultilineText,
    Password,
    String,
    migrate_to_password,
)
from cmk.rulesets.v1.rule_specs import SpecialAgent, Topic


def _kubernetes_form() -> Dictionary:
    return Dictionary(
        title=Title("Read the Longhorn CRDs through the Kubernetes API"),
        help_text=Help(
            "Preferred source. Reads nodes.longhorn.io, volumes.longhorn.io and instancemanagers.longhorn.io with "
            "a ServiceAccount token that may only get and list them. Also adds the services for volumes and "
            "instance managers. When set, the endpoint URL is not queried."
        ),
        elements={
            "api_server": DictElement(
                required=True,
                parameter_form=String(
                    title=Title("API server URL"),
                    help_text=Help("For example https://dev-homelab-k8s-api.example.net:6443"),
                ),
            ),
            "token": DictElement(
                required=True,
                parameter_form=Password(title=Title("ServiceAccount token"), migrate=migrate_to_password),
            ),
            "ca_cert": DictElement(
                required=False,
                parameter_form=MultilineText(
                    title=Title("CA certificate of the API server (PEM)"),
                    help_text=Help(
                        "The cluster's own CA (ca.crt of the token Secret). Without it, the API server is verified "
                        "against the system trust store."
                    ),
                ),
            ),
            "namespace": DictElement(
                required=False,
                parameter_form=String(
                    title=Title("Namespace of Longhorn"),
                    prefill=DefaultValue("longhorn-system"),
                ),
            ),
        },
    )


def _parameter_form() -> Dictionary:
    return Dictionary(
        title=Title("Longhorn disk capacity"),
        help_text=Help(
            "Queries a Longhorn deployment's node/disk capacity and health, and with the Kubernetes API as source "
            "also its volumes and instance managers. Use a host without a Checkmk agent and without an IP address: "
            "a special agent on a host with the normal agent replaces its agent connection."
        ),
        elements={
            "endpoint": DictElement(
                required=False,
                parameter_form=String(
                    title=Title("Endpoint URL (legacy)"),
                    help_text=Help(
                        "Base URL of the Longhorn API without a trailing slash, for example the environment's "
                        "Longhorn UI route http://longhorn.dev.iseja.net. That API has no authentication of its own. "
                        "Only used when the Kubernetes API is not configured."
                    ),
                ),
            ),
            "kubernetes": DictElement(required=False, parameter_form=_kubernetes_form()),
        },
    )


rule_spec_longhorn_capacity = SpecialAgent(
    name="longhorn_capacity",
    title=Title("Longhorn disk capacity"),
    topic=Topic.STORAGE,
    parameter_form=_parameter_form,
)
