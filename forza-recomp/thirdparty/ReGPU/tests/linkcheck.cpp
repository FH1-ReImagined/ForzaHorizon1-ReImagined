// Links ReGPU into an executable with nothing else, so any symbol it still expects from a game
// shows up as an unresolved external. Not meant to run.
#include "regpu/regpu.h"

int main()
{
    regpu::Start();
    regpu::SaveCaches();
}
