import asyncio
from opus_escrow.integrations.prembly import verify_bvn

# This will start the "You: " prompt in your terminal
print(asyncio.run(verify_bvn("tryout")))