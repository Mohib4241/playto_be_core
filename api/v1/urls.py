from django.urls import path
from api.v1.views.payout_views import (
    MerchantDashboardView,
    PayoutCreateView,
    PayoutDetailView,
    PayoutRetryView,
)

urlpatterns = [
    path('payouts/', PayoutCreateView.as_view(), name='payout-create'),
    path('payouts/<int:pk>/', PayoutDetailView.as_view(), name='payout-detail'),
    path('payouts/<int:pk>/retry/', PayoutRetryView.as_view(), name='payout-retry'),
    path('merchants/<int:pk>/dashboard/', MerchantDashboardView.as_view(), name='merchant-dashboard'),
]
