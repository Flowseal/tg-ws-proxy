import os
from time import sleep
import urllib.request as request

url = os.environ["LOG_URL"]
path_answer = ".github/scripts/answers"

for index in range(0,3):
    try:
        with request.urlopen(url,timeout=30.0) as r:
            log_bytes = r.read()
        break 
    except Exception:
        sleep(5.0)
else:
    print(str(path_answer + "/default.md"))
    exit()

log_old = log_bytes.decode("utf-8",errors="replace").splitlines()

worker_end = False
cfproxy_end = False

log = []
for line in reversed(log_old):
    line = line[20:]
    log.append(line)
    if "CF proxy:      enabled" in line:
        cfproxy_end = True
    elif "CF worker:     enabled" in line:
        worker_end = True
    elif "INFO   tg-ws-tray  Starting proxy" in line:
        break

secret_false = 0
timeouterror = 0
reset_error = 0 

for line in log :
    if "(wrong secret or proto)" in line:
        secret_false += 1 
        if secret_false >= 10:
            print(str(path_answer + "/wrong_secret.md"))
            break
    elif "TimeoutError" in line: 
        timeouterror += 1 
        if timeouterror >= 20:
            if cfproxy_end is False:
                print(str(path_answer + "/no_cfproxy.md"))
                break
            if worker_end is False:
                print(str(path_answer + "/timeout_no_worker.md"))
            else:
                print(str(path_answer + "/timeout.md"))                  
            break
    elif "ConnectionResetError" in line:
        reset_error += 1 
        if reset_error >= 20:
            print(str(path_answer + "/reset_error.md"))
            break
else: 
    if cfproxy_end is True:
        print(str(path_answer + "/default.md"))
    else:
        print(str(path_answer + "/no_cfproxy.md"))