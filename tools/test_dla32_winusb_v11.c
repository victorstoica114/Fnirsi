/* SPDX-License-Identifier: GPL-3.0-only */
#define libusb_bulk_transfer usb_fixture_bulk
#define libusb_claim_interface usb_fixture_claim
#define libusb_release_interface usb_fixture_release
#define libusb_close usb_fixture_close
#define sr_usb_open usb_fixture_open
#define main inherited_fixture_main
#include "../artifacts/dla32-winusb-v11-tests/test_dla32_winusb_v11_unit.c"
#undef main
#undef libusb_bulk_transfer
#undef libusb_claim_interface
#undef libusb_release_interface
#undef libusb_close
#undef sr_usb_open

static unsigned int usb_case,usb_calls,usb_closed,usb_released;
static unsigned char command_endpoint;
int usb_fixture_open(struct libusb_context *ctx,struct sr_usb_dev_inst *usb)
{ (void)ctx;usb->devhdl=(libusb_device_handle *)(uintptr_t)0x777;return SR_OK; }
int LIBUSB_CALL usb_fixture_claim(libusb_device_handle *handle,int iface)
{ (void)handle;return iface ? LIBUSB_ERROR_INVALID_PARAM : 0; }
int LIBUSB_CALL usb_fixture_release(libusb_device_handle *handle,int iface)
{ (void)handle;(void)iface;usb_released++;return 0; }
void LIBUSB_CALL usb_fixture_close(libusb_device_handle *handle)
{ (void)handle;usb_closed++; }
int LIBUSB_CALL usb_fixture_bulk(libusb_device_handle *handle,unsigned char endpoint,
        unsigned char *data,int length,int *transferred,unsigned int timeout)
{
    (void)handle;(void)timeout;usb_calls++;*transferred=length;
    if (endpoint==0x01) {
        command_endpoint=endpoint;
        if (length==512 && usb_case==1) { *transferred=0;return LIBUSB_ERROR_PIPE; }
        if (length==512 && usb_case==2) (*transferred)--;
        return 0;
    }
    if (endpoint==0x82) {
        memset(data,0,(size_t)length);data[0]=0x0a;data[1]=0x02;data[2]=0x0d;
        memcpy(data+13,usb_case==3?"DL16":"DL32",4);data[18]=0x0b;*transferred=usb_case==4?18:19;
        return usb_case==5?LIBUSB_ERROR_TIMEOUT:0;
    }
    *transferred=0;return LIBUSB_ERROR_PIPE;
}
int main(void)
{
    struct fixture f;uint8_t command[DLA_COMMAND_SIZE];
    if (inherited_fixture_main())return 1;
    checks=failures=0;
    for (usb_case=0;usb_case<=5;usb_case++) {
        fixture_init(&f,FALSE);f.devc->use_wch=FALSE;usb_calls=usb_closed=usb_released=0;
        if (!usb_case) {
            expect("libusb open verifies DL32 through OUT1 and IN2",dev_open(f.sdi)==SR_OK && usb_calls==2 && command_endpoint==0x01);
            dla_stop(command);
            expect("libusb capture commands use the verified OUT1 endpoint",send_command(f.sdi,command)==SR_OK && command_endpoint==0x01);
        } else {
            expect("failed query/short reply/wrong model rejects libusb open",dev_open(f.sdi)!=SR_OK &&
                !((struct sr_usb_dev_inst *)f.sdi->conn)->devhdl && usb_closed==1 && usb_released==1);
        }
        fixture_clear(&f);
    }
    printf("WinUSB endpoint audit: %u checks, %u failures.\n",checks,failures);
    return failures?1:0;
}
