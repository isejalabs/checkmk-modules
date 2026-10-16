from cmk.rulesets.v1 import Help, Title
from cmk.rulesets.v1.form_specs import DictElement, Dictionary, String
from cmk.rulesets.v1.rule_specs import SpecialAgent, Topic


def _parameter_form() -> Dictionary:
    return Dictionary(
        title=Title("Longhorn disk capacity"),
        help_text=Help(
            "Queries a Longhorn deployment's node/disk capacity and health through its API. The Longhorn API "
            "has no authentication of its own, so no identity is configured here. Use a host without a Checkmk "
            "agent and without an IP address: a special agent on a host with the normal agent replaces its "
            "agent connection."
        ),
        elements={
            "endpoint": DictElement(
                required=True,
                parameter_form=String(
                    title=Title("Endpoint URL"),
                    help_text=Help(
                        "Base URL without a trailing slash, for example the environment's existing Longhorn UI "
                        "route: http://longhorn.dev.iseja.net"
                    ),
                ),
            ),
        },
    )


rule_spec_longhorn_capacity = SpecialAgent(
    name="longhorn_capacity",
    title=Title("Longhorn disk capacity"),
    topic=Topic.STORAGE,
    parameter_form=_parameter_form,
)
