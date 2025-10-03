# Use this script to run tshark to capture a log file which can then be processed
# using decode.
#
# Parameters:
#  if 	The interface to capture on (typically Ethernet)
#  ip 	The Ruida controller IP address.
#  out  The capture file name -- not including extension. This saves to $2.log.
# WARNING: No parameter checking.
param (
	[string]$if, # The interface to capture on.
	[string]$ip, # IP address of controller.
	[string]$out # Output file name (excluding extension).
)
tshark -Y "(ip.addr == $ip)" -i $if -l -T fields -e frame.time_delta -e udp.port -e udp.length -e data.data | tee $out
