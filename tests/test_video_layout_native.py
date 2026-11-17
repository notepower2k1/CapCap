"""Opt-in GPU regression: replace cinematic image backgrounds in real MPV."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest


@unittest.skipUnless(os.name == 'nt' and os.environ.get('CAPCAP_TEST_NATIVE_LAYOUT') == '1',
                     'Set CAPCAP_TEST_NATIVE_LAYOUT=1 to test the native Windows player')
class NativeLayoutTests(unittest.TestCase):
    def test_repeated_4k_image_changes_finish_without_unbounded_memory_growth(self):
        env = dict(os.environ)
        env.pop('QT_QPA_PLATFORM',None)
        probe = Path(__file__).with_name('video_layout_native_probe.py')
        result = subprocess.run([sys.executable,str(probe)],cwd=probe.parent.parent,env=env,
                                capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=45)
        self.assertEqual(result.returncode,0,(result.stdout+result.stderr)[-5000:])
        line = next((line for line in result.stdout.splitlines() if line.startswith('RESULT ')),None)
        self.assertIsNotNone(line,result.stdout[-3000:])
        samples = json.loads(line.removeprefix('RESULT '))
        self.assertEqual(len(samples),36)
        self.assertLess(max(samples[-6:])-min(samples[6:12]),180)


if __name__ == '__main__':
    unittest.main()
