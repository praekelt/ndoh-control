from django.shortcuts import render
from django.contrib.auth.decorators import login_required
# from controlinterface.forms import AuthLoginForm

@login_required(login_url='/controlinterface/login/')
def index(request):
    return render(request,
                  'controlinterface/index.html')

