import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from yamaha01v96.params import ParameterMap
from yamaha01v96 import sysex

pm = ParameterMap()
print("groups loaded:", len(pm.groups))

pd = pm.get("kInputEQ", "kEQLowG")
print("kEQLowG:", pd)

msg = sysex.build_parameter_change(pd.element, pd.param, channel=0, value=-90, min_value=pd.min, max_value=pd.max)
print("change msg:", msg.hex(" "))

req = sysex.build_parameter_request(pd.element, pd.param, channel=0)
print("request msg:", req.hex(" "))

data = sysex.encode_value(-90, pd.min, pd.max)
back = sysex.decode_value(data, pd.min, pd.max)
print("encode(-90) ->", data, "-> decode ->", back)

pd2 = pm.get("kInputComp", "kCompThreshold")
print("kCompThreshold:", pd2)
d2 = sysex.encode_value(-260, pd2.min, pd2.max)
print("encode(-260):", d2, "-> decode:", sysex.decode_value(d2, pd2.min, pd2.max))

pd3 = pm.get("kInputChannelName", "kChannelNameShort1")
print("kChannelNameShort1:", pd3)
name_msg = sysex.build_parameter_change(
    pd3.element, pd3.param, channel=0, value=65, min_value=pd3.min, max_value=pd3.max,
    model_id=pd3.model_id, addr_type=pd3.addr_type,
)
print("name change msg:", name_msg.hex(" "))


pd4 = pm.get("kEffect", "kEffectParam1")
print("kEffectParam1:", pd4)
d4 = sysex.encode_value(360, pd4.min, pd4.max)
print("encode(360):", d4, "-> decode:", sysex.decode_value(d4, pd4.min, pd4.max))
