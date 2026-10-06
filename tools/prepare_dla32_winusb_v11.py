"""Correct the libusb command endpoint and require fresh DL32 identification."""
from pathlib import Path
import shutil
from prepare_dla32_worker_v8 import replace_once

ROOT=Path(__file__).resolve().parent.parent


def main():
    source=ROOT/'artifacts/dla32-winusb-v11-source/libsigrok'
    tests=ROOT/'artifacts/dla32-winusb-v11-tests'
    if source.exists() or tests.exists():raise RuntimeError('Fresh V11 source/tests required')
    shutil.copytree(ROOT/'artifacts/dla32-wch-worker-v7-r2-source/libsigrok',source);tests.mkdir()
    api=source/'src/hardware/fnirsi-dla32/api.c';text=api.read_text()
    text=replace_once(text,'#define NUM_TRANSFERS 8','#define NUM_TRANSFERS 8\n#define DLA_COMMAND_ENDPOINT 0x01\n#define DLA_IDENTIFICATION_ENDPOINT 0x82')
    text=replace_once(text,'int ret = sr_usb_open(drvc->sr_ctx->libusb_ctx, usb);',
        '''uint8_t identification[512] = {0x0a, 0x10}, reply[512];
    int count;
    int ret = sr_usb_open(drvc->sr_ctx->libusb_ctx, usb);''')
    anchor='''	return SR_OK;
}

static int send_command'''
    replacement='''    /* The shared WCH VID/PID does not identify the analyzer model. */
    count = 0;
    ret = libusb_bulk_transfer(usb->devhdl, DLA_COMMAND_ENDPOINT,
        identification, sizeof(identification), &count, 500);
    if (!ret && count == (int)sizeof(identification)) {
        count = 0;
        ret = libusb_bulk_transfer(usb->devhdl, DLA_IDENTIFICATION_ENDPOINT,
            reply, sizeof(reply), &count, 500);
        if (!ret && count >= 19 && reply[0] == 0x0a && reply[1] == 0x02 &&
            reply[2] == 0x0d && !memcmp(reply + 13, "DL32", 4) && reply[18] == 0x0b)
            return SR_OK;
    }
    sr_err("libusb device did not confirm DL32 identity.");
    libusb_release_interface(usb->devhdl, 0);
    libusb_close(usb->devhdl);
    usb->devhdl = NULL;
    return SR_ERR_NA;
}

static int send_command'''
    text=replace_once(text,anchor,replacement)
    text=replace_once(text,'libusb_bulk_transfer(usb->devhdl, 0x02, (uint8_t *)packet,',
        'libusb_bulk_transfer(usb->devhdl, DLA_COMMAND_ENDPOINT, (uint8_t *)packet,')
    api.write_text(text,newline='\n')
    fixture=(ROOT/'artifacts/dla32-wch-worker-v7-r2-tests/test_dla32_wch_worker_v7_r2_unit.c').read_text().replace('dla32-wch-worker-v7-r2-source','dla32-winusb-v11-source')
    (tests/'test_dla32_winusb_v11_unit.c').write_text(fixture,newline='\n')
    msys=Path((ROOT/'tools/toolchain-path.txt').read_text().strip())
    old=msys/'tmp/dla32-wch-worker-v7-r2-build/libsigrok';new=msys/'tmp/dla32-winusb-v11-build/libsigrok'
    if new.exists():raise RuntimeError('Fresh V11 native build required')
    shutil.copytree(old,new)
    for path in new.rglob('*'):
        if path.is_file() and (path.name in ('Makefile','config.status','libtool','libsigrok.pc','libsigrokcxx.pc') or path.suffix in ('.Plo','.Po','.la','.lo')):
            path.write_text(path.read_text().replace('dla32-wch-worker-v7-r2-','dla32-winusb-v11-'),newline='\n')
    api.touch();print('V11 endpoint/identity source prepared; WCH lifecycle code unchanged.')


if __name__=='__main__':main()
