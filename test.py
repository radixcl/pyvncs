from lib import common
import time

if __name__ == '__main__':
    #c = ["/bin/ls", "-alh"]
    #c = ["/bin/sleep", "5"]
    c = ["./test.sh"]
    r = common.proc()
    r.run(c)
    print("PID", r.getpid())
    #print("Waiting...")
    #r.waitproc()
    time.sleep(1)
    r.terminate()
    del r
    print("DONE")
