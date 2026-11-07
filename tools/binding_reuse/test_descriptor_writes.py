"""Source integration guard for the descriptor-reuse rendering regression.

Payload equality tests passed while foreground rendering was corrupt. Keep
UpdateBindings outside this experiment: texture sets must follow the normal
allocation/write/bind path. This guard is not a substitute for a GPU capture.
"""
from pathlib import Path
import re
import sys
import unittest


class DescriptorWrites(unittest.TestCase):
    def test_reuse_cannot_skip_texture_descriptor_writes(self):
        source = (Path(sys.argv[1]) /
                  "src/graphics/vulkan/command_processor.cpp").read_text()
        body = source.split("bool VulkanCommandProcessor::UpdateBindings(", 1)[1]
        body = body.split("uint32_t VulkanCommandProcessor::WriteTransientTextureBindings(", 1)[0]
        self.assertNotIn("BindingReuseEnabled", body)
        self.assertNotIn("texture_descriptor_reuse_", source)
        for stage in ("vertex", "pixel"):
            flag = f"write_{stage}_textures"
            # Once derived from invalidated values, the write decision cannot
            # be suppressed by a remembered image/sampler payload.
            self.assertEqual(len(re.findall(r"\b" + flag + r"\s*=(?!=)", body)), 1)
            self.assertIn(f"if ({flag})", body)
        self.assertIn("dfn.vkUpdateDescriptorSets(", body)
        self.assertIn("deferred_command_buffer_.CmdVkBindDescriptorSets(", body)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: test_descriptor_writes.py SDK_SOURCE")
    unittest.main(argv=[sys.argv[0]])
