from cmk.rulesets.v1 import Help, Title
from cmk.rulesets.v1.form_specs import (
    DictElement,
    Dictionary,
    List,
    Password,
    String,
    migrate_to_password,
)
from cmk.rulesets.v1.rule_specs import SpecialAgent, Topic


def _parameter_form() -> Dictionary:
    return Dictionary(
        title=Title("RustFS bucket quota"),
        help_text=Help(
            "Queries the quota statistics of RustFS buckets through the admin API. The identity needs only the "
            "bucket-scoped permission s3:GetBucketQuota. Use a host without a Checkmk agent and without an IP "
            "address: a special agent on a host with the normal agent replaces its agent connection."
        ),
        elements={
            "endpoint": DictElement(
                required=True,
                parameter_form=String(
                    title=Title("Endpoint URL"),
                    help_text=Help("Base URL without a trailing slash, for example https://rustfs.example.net:9000"),
                ),
            ),
            "access_key": DictElement(required=True, parameter_form=String(title=Title("Access key"))),
            "secret_key": DictElement(
                required=True,
                parameter_form=Password(title=Title("Secret key"), migrate=migrate_to_password),
            ),
            "buckets": DictElement(
                required=True,
                parameter_form=List(
                    title=Title("Buckets"),
                    element_template=String(title=Title("Bucket name")),
                ),
            ),
        },
    )


rule_spec_rustfs_quota = SpecialAgent(
    name="rustfs_quota",
    title=Title("RustFS bucket quota"),
    topic=Topic.STORAGE,
    parameter_form=_parameter_form,
)
