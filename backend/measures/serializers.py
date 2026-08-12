from rest_framework import serializers

from .models import MeasureImpact


class MeasureImpactSerializer(serializers.ModelSerializer):
    class Meta:
        model = MeasureImpact
        fields = ["portfolio", "agency", "direction", "fiscal_year", "amount_thousands"]
