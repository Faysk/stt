import os
import subprocess
from dotenv import load_dotenv

load_dotenv()
env=os.environ.copy()
env['TUNNEL_TOKEN']=os.environ['TTS_TUNNEL_TOKEN']
subprocess.run([os.environ['TTS_CLOUDFLARED'],'tunnel','--no-autoupdate','run'],env=env,check=True)
