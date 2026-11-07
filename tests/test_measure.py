import unittest
import numpy as np
from nk_dho924s_mcp.scope import Waveform, is_dangerous


def make(f, amp, n=1000, xinc=1e-5, shape="sine"):
    t = np.arange(n) * xinc
    ph = 2 * np.pi * f * t + 0.4
    v = amp * (np.sin(ph) if shape == "sine" else 2 / np.pi * np.arcsin(np.sin(ph)))
    raw = np.clip(np.round(128 + v / 0.01), 0, 255).astype(np.uint8)
    return Waveform("CHAN1", (raw.astype(float) - 128) * 0.01, raw, xinc, 0.0)


class T(unittest.TestCase):
    def test_sine(self):
        m = make(1234.0, 0.5).measure()
        self.assertAlmostEqual(m["frequency_hz"], 1234.0, delta=5)
        self.assertAlmostEqual(m["fundamental_vpp"], 1.0, delta=0.02)
        self.assertAlmostEqual(m["crest_factor"], 1.414, delta=0.05)
        self.assertLess(m["thd_n_pct"], 3)

    def test_triangle_fundamental_is_81pct(self):
        m = make(1000.0, 0.5, shape="tri").measure(1000.0)
        self.assertAlmostEqual(m["vpp"], 1.0, delta=0.03)
        self.assertAlmostEqual(m["fundamental_vpp"], 8 / np.pi ** 2, delta=0.02)
        self.assertAlmostEqual(m["crest_factor"], 1.732, delta=0.05)

    def test_blocklist(self):
        self.assertTrue(is_dangerous(":MEAS:ITEM? VPP,CHAN1"))
        self.assertTrue(is_dangerous("meas:item? freq,chan2"))
        self.assertFalse(is_dangerous(":MEAS:ITEM VPP,CHAN1"))
        self.assertFalse(is_dangerous(":CHAN1:SCAL?"))


if __name__ == "__main__":
    unittest.main()
