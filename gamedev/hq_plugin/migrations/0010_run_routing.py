"""Historical selections deliberately remain unknown; no data backfill."""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("gamedev_hq", "0009_projectprofile_source_apply_scope")]
    operations = [
        migrations.AddField(model_name="run", name=name, field=models.CharField(max_length=size, blank=True))
        for name, size in (
            ("requested_profile", 20),
            ("selected_model", 40),
            ("selected_effort", 20),
            ("routing_reason", 120),
        )
    ] + [
        migrations.AddField(
            model_name="run",
            name="escalation_root",
            field=models.ForeignKey(
                null=True,
                blank=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="escalations",
                to="gamedev_hq.run",
            ),
        )
    ]
