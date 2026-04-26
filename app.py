import aws_cdk as cdk

from platform_lab.data_tier_stack import DataTierStack
from platform_lab.app_tier_stack import AppTierStack
from platform_lab.presentation_stack import PresentationStack
from platform_lab.alarms_stack import AlarmsStack

app = cdk.App()

env = cdk.Environment(account="820242933814", region="ap-southeast-2")

data_tier = DataTierStack(app, "DataTierStack", env=env)
app_tier = AppTierStack(app, "AppTierStack", data_tier=data_tier, env=env)
presentation = PresentationStack(app, "PresentationStack", app_tier=app_tier, env=env)
alarms = AlarmsStack(app, "AlarmsStack", env=env)
# AlarmsStack reads /ops-lab/3tier/alb-full-name and target-group-full-name from SSM
# at deploy time — must wait for PresentationStack (and its dependency chain) to finish first.
alarms.add_dependency(presentation)

for stack in [data_tier, app_tier, presentation, alarms]:
    cdk.Tags.of(stack).add("Project", "ops-lab")
    cdk.Tags.of(stack).add("Stack", "3tier")

app.synth()
