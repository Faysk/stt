import os
import subprocess
from dotenv import load_dotenv
from server.config import required

load_dotenv()
env=os.environ.copy()
env['TUNNEL_TOKEN']=required('STT_TUNNEL_TOKEN')
subprocess.run([required('STT_CLOUDFLARED'),'tunnel','--no-autoupdate','run'],env=env,check=True)
