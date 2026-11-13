#!/usr/bin/env python3
from lh2_monitor.credentials import caen_credentials, load_environment

host = '192.168.20.51' # caenhv1
systype = 'SY1527'
linktype = 'TCPIP'

#______________________________________________________________________________
def main():
  load_environment()
  username, password = caen_credentials()
  from caen_libs import caenhvwrapper as hv

  with hv.Device.open(hv.SystemType[systype], hv.LinkType[linktype],
                      host, username, password) as device:
    slots = device.get_crate_map()
    for board in slots:
      if board is None or board.slot != 4:
        continue
      for ch in range(board.n_channel):
        print(f'{board.slot:02d}.{ch:04d} Pw OFF')
        device.set_ch_param(board.slot, [ch], 'Pw', 0)

#______________________________________________________________________________
if __name__ == '__main__':
  main()
