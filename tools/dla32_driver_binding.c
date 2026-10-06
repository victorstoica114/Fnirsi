/* SPDX-License-Identifier: GPL-3.0-only */
/* Select one existing signed compatible INF for one exact DLA instance.
 * Inspect does not install. Original OEM package is retained for rollback.
 * https://learn.microsoft.com/windows/win32/api/newdev/nf-newdev-diinstalldevice
 */
#ifndef UNICODE
#define UNICODE
#endif
#ifndef _UNICODE
#define _UNICODE
#endif
#include <windows.h>
#include <setupapi.h>
#include <newdev.h>
#include <stdio.h>
#include <string.h>
#include <wchar.h>

static const wchar_t *device=L"USB\\VID_1A86&PID_5537\\0123456789";
static const wchar_t *root=L"D:\\Documente\\analizor logic\\";

static BOOL admin(void)
{
    SID_IDENTIFIER_AUTHORITY authority=SECURITY_NT_AUTHORITY;PSID sid=NULL;BOOL member=FALSE;
    if (AllocateAndInitializeSid(&authority,2,SECURITY_BUILTIN_DOMAIN_RID,DOMAIN_ALIAS_RID_ADMINS,0,0,0,0,0,0,&sid)) {
        CheckTokenMembership(NULL,sid,&member);FreeSid(sid);
    }
    return member;
}

int wmain(int argc,wchar_t **argv)
{
    SP_DEVINFO_DATA info={0};SP_DEVINSTALL_PARAMS_W params={0};SP_DRVINFO_DATA_W driver={0};
    SP_DRVINFO_DETAIL_DATA_W *detail=NULL;HDEVINFO set=INVALID_HANDLE_VALUE;
    wchar_t directory[MAX_PATH],inf[MAX_PATH],output[32768],service[256];
    DWORD type,size,error=0,index;BOOL install,reboot=FALSE,selected=FALSE,success=FALSE;
    HANDLE mutex=NULL,file=INVALID_HANDLE_VALUE;const char *phase="arguments";char result[2048];DWORD written;
    HMODULE installer=NULL;
    BOOL (WINAPI *install_device)(HWND,HDEVINFO,PSP_DEVINFO_DATA,PSP_DRVINFO_DATA_W,DWORD,PBOOL)=NULL;
    if (argc!=4 || (wcscmp(argv[1],L"--inspect") && wcscmp(argv[1],L"--install")) ||
        (wcscmp(argv[2],L"winusb") && wcscmp(argv[2],L"wch")))return 2;
    install=!wcscmp(argv[1],L"--install");
    if (!GetFullPathNameW(argv[3],32768,output,NULL) || _wcsnicmp(output,root,wcslen(root)))return 2;
    file=CreateFileW(output,GENERIC_WRITE,FILE_SHARE_READ,NULL,CREATE_NEW,FILE_ATTRIBUTE_NORMAL,NULL);
    if (file==INVALID_HANDLE_VALUE)return 2;
    phase="hardware ownership";
    mutex=CreateMutexW(NULL,FALSE,L"Local\\FNIRSI_DLA32_BOUNDARY_RUNNER");
    if (!mutex || WaitForSingleObject(mutex,0)!=WAIT_OBJECT_0) {error=ERROR_BUSY;goto cleanup;}
    phase="exact device open";set=SetupDiCreateDeviceInfoList(NULL,NULL);info.cbSize=sizeof(info);
    if (set==INVALID_HANDLE_VALUE || !SetupDiOpenDeviceInfoW(set,device,NULL,0,&info))goto failure;
    phase="service check";
    if (!SetupDiGetDeviceRegistryPropertyW(set,&info,SPDRP_SERVICE,&type,(PBYTE)service,sizeof(service),&size))goto failure;
    if (_wcsicmp(service,L"CH375_A64") && _wcsicmp(service,L"WinUSB")) {error=ERROR_INVALID_DATA;goto cleanup;}
    if (install && !admin()) {error=ERROR_ACCESS_DENIED;goto cleanup;}
    phase="single existing INF";
    if (!GetWindowsDirectoryW(directory,MAX_PATH))goto failure;
    _snwprintf(inf,MAX_PATH,L"%ls\\INF\\%ls",directory,!wcscmp(argv[2],L"winusb")?L"winusb.inf":L"oem166.inf");
    params.cbSize=sizeof(params);
    if (!SetupDiGetDeviceInstallParamsW(set,&info,&params))goto failure;
    params.Flags|=DI_ENUMSINGLEINF|DI_QUIETINSTALL;
    params.FlagsEx|=DI_FLAGSEX_ALLOWEXCLUDEDDRVS;
    wcsncpy(params.DriverPath,inf,MAX_PATH-1);params.DriverPath[MAX_PATH-1]=0;
    if (!SetupDiSetDeviceInstallParamsW(set,&info,&params) ||
        !SetupDiBuildDriverInfoList(set,&info,SPDIT_COMPATDRIVER))goto failure;
    detail=HeapAlloc(GetProcessHeap(),HEAP_ZERO_MEMORY,65536);
    if (!detail) {error=ERROR_NOT_ENOUGH_MEMORY;goto cleanup;}
    phase="compatible driver selection";
    for (index=0;;index++) {
        driver.cbSize=sizeof(driver);
        if (!SetupDiEnumDriverInfoW(set,&info,SPDIT_COMPATDRIVER,index,&driver))break;
        detail->cbSize=sizeof(*detail);
        if (!SetupDiGetDriverInfoDetailW(set,&info,&driver,detail,65536,NULL))continue;
        if (_wcsicmp(detail->InfFileName,inf))continue;
        if (!wcscmp(argv[2],L"winusb") && (_wcsicmp(driver.ProviderName,L"Microsoft") || _wcsnicmp(detail->SectionName,L"WINUSB",6)))continue;
        if (!wcscmp(argv[2],L"wch") && _wcsnicmp(detail->SectionName,L"CH375.Install",13))continue;
        selected=TRUE;break;
    }
    if (!selected) {error=ERROR_NO_MORE_ITEMS;goto cleanup;}
    if (install) {
        phase="install selected existing driver";
        installer=LoadLibraryExW(L"newdev.dll",NULL,LOAD_LIBRARY_SEARCH_SYSTEM32);
        if (installer) {
            FARPROC address=GetProcAddress(installer,"DiInstallDevice");
            memcpy(&install_device,&address,sizeof(install_device));
        }
        if (!install_device || !install_device(NULL,set,&info,&driver,0,&reboot))goto failure;
        phase="verify service after install";
        if (!SetupDiGetDeviceRegistryPropertyW(set,&info,SPDRP_SERVICE,&type,(PBYTE)service,sizeof(service),&size))goto failure;
        if (_wcsicmp(service,!wcscmp(argv[2],L"winusb")?L"WinUSB":L"CH375_A64")) {error=ERROR_INVALID_DATA;goto cleanup;}
    }
    phase="complete";success=TRUE;goto cleanup;
failure:error=GetLastError();
cleanup:
    _snprintf(result,sizeof(result),"{\"passed\":%s,\"install_requested\":%s,\"target\":\"%ls\",\"compatible_driver_selected\":%s,\"reboot_required\":%s,\"phase\":\"%s\",\"Windows_error\":%lu,\"other_devices_targeted\":false,\"OEM_package_removed\":false}\n",
        success?"true":"false",install?"true":"false",argv[2],selected?"true":"false",reboot?"true":"false",phase,error);
    WriteFile(file,result,(DWORD)strlen(result),&written,NULL);CloseHandle(file);
    if (detail)HeapFree(GetProcessHeap(),0,detail);
    if (installer)FreeLibrary(installer);
    if (set!=INVALID_HANDLE_VALUE)SetupDiDestroyDeviceInfoList(set);
    if (mutex) {ReleaseMutex(mutex);CloseHandle(mutex);}
    printf("%s: %s, Windows error %lu, reboot %d.\n",success?"PASS":"FAIL",phase,error,reboot);
    return success?0:1;
}
