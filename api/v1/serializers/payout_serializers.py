from rest_framework import serializers
from api.models import Payout, Merchant, Ledger

class MerchantSerializer(serializers.ModelSerializer):
    balance_paise = serializers.SerializerMethodField()

    class Meta:
        model = Merchant
        fields = ['id', 'name', 'balance_paise']

    def get_balance_paise(self, obj):
        from api.v1.services.payout_service import LedgerService
        return LedgerService.get_balance(obj)

class PayoutSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payout
        fields = '__all__'

class LedgerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Ledger
        fields = '__all__'
