from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from api.v1.services.system_service import SystemService

class ResetDatabaseView(APIView):
    def post(self, request):
        try:
            SystemService.reset_system()
            return Response({"message": "System reset successfully (Database + Broker)"}, status=status.HTTP_200_OK)
        except Exception as e:
            return Response({"error": f"Failed to reset system: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
