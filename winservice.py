import win32service
import win32serviceutil
import win32api
import win32con
import win32event
import win32evtlogutil
import servicemanager
import os
import sys
from lib import const
from lib import common
from win32api import OutputDebugString as ODS
import traceback


const.SERVICENAME = "Test Service"
const.SERVICEDNAME = "Test Service"
const.SERVICEDESC = "Test Service Description"

const.CHILD = [
    "C:\\Program Files\\Python36\\python.exe",
    "C:\\pyvncs\\ctrlsrv.py",
    "-P",
    "kaka80"
]

class service(win32serviceutil.ServiceFramework):
   
    _svc_name_ = const.SERVICENAME
    _svc_display_name_ = const.SERVICEDNAME
    _svc_description_ = const.SERVICEDESC
         
    def __init__(self, args):
        win32serviceutil.ServiceFramework.__init__(self, args)
        self.hWaitStop = win32event.CreateEvent(None, 0, 0, None)           

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        win32event.SetEvent(self.hWaitStop)                    
         
    def SvcDoRun(self):
        servicemanager.LogMsg(servicemanager.EVENTLOG_INFORMATION_TYPE,servicemanager.PYS_SERVICE_STARTED,(self._svc_name_, '')) 
        self.timeout = 3000

        servicemanager.LogInfoMsg("%s - is running 1" % const.SERVICENAME)
        r = common.proc()
        try:
            r.run(const.CHILD)
        except:
            servicemanager.LogInfoMsg("ERROR: %s" % sys.exc_info()[0])
            servicemanager.LogInfoMsg(traceback.format_exc())
            sys.exit(1)

        newpid = r.getpid()
        servicemanager.LogInfoMsg("%s - started child with pid %s" % (const.SERVICENAME, newpid))
           
        while True:
            # Wait for service stop signal, if I timeout, loop again
            rc = win32event.WaitForSingleObject(self.hWaitStop, self.timeout)
            # Check to see if self.hWaitStop happened
            if rc == win32event.WAIT_OBJECT_0:
                # Stop signal encountered
                servicemanager.LogInfoMsg("%s - STOPPED" % const.SERVICENAME)
                r.terminate()
                break
            #else:
            #    servicemanager.LogInfoMsg("%s - still running" % const.SERVICENAME)

      
def ctrlHandler(ctrlType):
    return True
                  
if __name__ == '__main__':
    ODS("__main__\n")
    servicemanager.LogInfoMsg("TEST")
    appdir = os.path.abspath(os.path.dirname(sys.argv[0]))
    os.chdir(appdir)
    win32api.SetConsoleCtrlHandler(ctrlHandler, True)   
    win32serviceutil.HandleCommandLine(service)
