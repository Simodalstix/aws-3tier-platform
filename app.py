import aws_cdk as cdk

from platform_lab.data_tier_stack import DataTierStack
from platform_lab.app_tier_stack import AppTierStack
from platform_lab.presentation_stack import PresentationStack

app = cdk.App()

env = cdk.Environment(account="820242933814", region="ap-southeast-2")

data_tier = DataTierStack(app, "DataTierStack", env=env)
app_tier = AppTierStack(app, "AppTierStack", data_tier=data_tier, env=env)
presentation = PresentationStack(app, "PresentationStack", app_tier=app_tier, env=env)

for stack in [data_tier, app_tier, presentation]:
    cdk.Tags.of(stack).add("Project", "ops-lab")
    cdk.Tags.of(stack).add("Stack", "3tier")

app.synth()
