import json
from typing import Any, Dict

from bson import ObjectId
from anthropic import AsyncAnthropic

from opus_escrow.config import get_settings

from opus_escrow.integrations.telegram import send_telegram_message_by_phone

from opus_escrow.repositories.users import (
    get_or_create_user,
    update_profile,
    request_verification,
)

from opus_escrow.repositories.transactions import (
    get_transaction_by_ref,
    create_transaction,
    accept_transaction,
    decline_transaction,
    mark_delivered,
    raise_dispute,
    generate_payment_account,
    initiate_payout,
    confirm_payout,
)


settings = get_settings()


MODEL_ID = "claude-sonnet-4-6"

SYSTEM_PROMPT = """
You are Opus Escrow AI, a secure escrow assistant.

Identify every user by their registered phone number.
Never assume a specific messaging platform.

MANDATORY FLOW:

1. Get the user's phone number and call get_or_create_user.

2. If they are new or missing profile details, collect:
- first name
- last name
- location

Then call update_profile.

3. If verification_status is not verified:
Ask for BVN and call request_verification.

Do NOT proceed to transactions until verification_status is verified.

4. Once both parties are verified:
Help create or respond to escrow transactions.

Always use transaction references:
OPUS-XXXXXX.

5. When a transaction is created:
Proactively call send_telegram_message to notify the counterparty.

6. Continue the flow:
accept -> generate payment account -> delivery -> payout confirmation.

Always use transaction references.
"""


# ============================================================
# CLAUDE TOOL DEFINITIONS
# ============================================================

TOOLS = [

{
"name": "get_or_create_user",
"description": "Lookup or create a user by their registered phone number. Use this first.",
"input_schema": {
"type": "object",
"properties": {
"whatsapp_number": {
"type": "string",
"description": "The user's phone number."
}
},
"required": [
"whatsapp_number"
]
}
},


{
"name": "create_transaction",
"description": "Creates a new escrow transaction.",
"input_schema": {
"type": "object",
"properties": {
"initiator_id":{"type":"string"},
"counterparty_id":{"type":"string"},
"buyer_id":{"type":"string"},
"seller_id":{"type":"string"},
"item_description":{"type":"string"},
"amount":{"type":"number"},
"currency":{"type":"string"}
},
"required":[
"initiator_id",
"counterparty_id",
"buyer_id",
"seller_id",
"item_description",
"amount"
]
}
},


{
"name":"get_transaction_by_ref",
"description":"Retrieves transaction details using OPUS reference.",
"input_schema":{
"type":"object",
"properties":{
"transaction_ref":{
"type":"string"
}
},
"required":["transaction_ref"]
}
},


{
"name":"accept_transaction",
"description":"Accepts a pending escrow transaction.",
"input_schema":{
"type":"object",
"properties":{
"transaction_ref":{
"type":"string"
}
},
"required":["transaction_ref"]
}
},


{
"name":"decline_transaction",
"description":"Declines a pending escrow transaction.",
"input_schema":{
"type":"object",
"properties":{
"transaction_ref":{
"type":"string"
}
},
"required":["transaction_ref"]
}
},


{
"name":"generate_payment_account",
"description":"Generates a payment account for escrow funding.",
"input_schema":{
"type":"object",
"properties":{
"transaction_ref":{
"type":"string"
}
},
"required":["transaction_ref"]
}
},


{
"name":"mark_delivered",
"description":"Marks goods/service as delivered.",
"input_schema":{
"type":"object",
"properties":{
"transaction_ref":{
"type":"string"
}
},
"required":["transaction_ref"]
}
},


{
"name":"raise_dispute",
"description":"Raises a dispute and freezes transaction.",
"input_schema":{
"type":"object",
"properties":{
"transaction_ref":{"type":"string"},
"reason":{"type":"string"},
"actor":{"type":"string"}
},
"required":[
"transaction_ref",
"reason",
"actor"
]
}
},


{
"name":"initiate_payout",
"description":"Initiates seller payout.",
"input_schema":{
"type":"object",
"properties":{
"transaction_ref":{"type":"string"},
"account_number":{"type":"string"},
"bank_code":{"type":"string"},
"account_name":{"type":"string"},
"opus_fee":{"type":"number"}
},
"required":[
"transaction_ref",
"account_number",
"bank_code",
"account_name",
"opus_fee"
]
}
},


{
"name":"confirm_payout",
"description":"Checks payout status.",
"input_schema":{
"type":"object",
"properties":{
"transaction_ref":{
"type":"string"
}
},
"required":[
"transaction_ref"
]
}
},


{
"name":"send_telegram_message",
"description":"Send a Telegram notification to a registered user.",
"input_schema":{
"type":"object",
"properties":{
"to_number":{
"type":"string"
},
"message":{
"type":"string"
}
},
"required":[
"to_number",
"message"
]
}
},


{
"name":"update_profile",
"description":"Updates user profile details.",
"input_schema":{
"type":"object",
"properties":{
"user_id":{"type":"string"},
"first_name":{"type":"string"},
"last_name":{"type":"string"},
"location":{"type":"string"}
},
"required":[
"user_id"
]
}
},


{
"name":"request_verification",
"description":"Requests BVN verification.",
"input_schema":{
"type":"object",
"properties":{
"user_id":{"type":"string"},
"method":{"type":"string"},
"verification_number":{"type":"string"}
},
"required":[
"user_id",
"method",
"verification_number"
]
}
}

]


FUNCTION_MAP = {

"get_or_create_user": get_or_create_user,
"update_profile": update_profile,
"request_verification": request_verification,

"create_transaction": create_transaction,
"get_transaction_by_ref": get_transaction_by_ref,
"accept_transaction": accept_transaction,
"decline_transaction": decline_transaction,
"mark_delivered": mark_delivered,
"raise_dispute": raise_dispute,

"generate_payment_account": generate_payment_account,
"initiate_payout": initiate_payout,
"confirm_payout": confirm_payout,

"send_telegram_message": send_telegram_message_by_phone,

}


# ============================================================
# CLAUDE CLASS
# ============================================================

class ClaudeLLM:

    def __init__(self):

        self.client = AsyncAnthropic(
            api_key=settings.anthropic_api_key
        )

        self.messages = []


    async def send(self, message:str) -> str:


        self.messages.append(
            {
                "role":"user",
                "content":message
            }
        )


        while True:

            response = await self.client.messages.create(

                model=MODEL_ID,

                max_tokens=2048,

                system=SYSTEM_PROMPT,

                messages=self.messages,

                tools=TOOLS
            )


            self.messages.append(
                {
                    "role":"assistant",
                    "content":response.content
                }
            )


            tool_calls = [
                block for block in response.content
                if block.type == "tool_use"
            ]


            if not tool_calls:

                return response.content[0].text



            tool_results = []


            for tool in tool_calls:

                result = await self._execute_function(
                    tool.name,
                    tool.input
                )


                tool_results.append(
                    {
                        "type":"tool_result",
                        "tool_use_id":tool.id,
                        "content":json.dumps(result)
                    }
                )


            self.messages.append(
                {
                    "role":"user",
                    "content":tool_results
                }
            )



    async def _execute_function(
        self,
        name:str,
        fc_args:Any
    ) -> Dict[str,Any]:

        print(
            f"\n[CLAUDE TOOL CALL] {name}({fc_args})"
        )


        if name not in FUNCTION_MAP:
            return {
                "error":f"{name} not found"
            }


        try:

            args = dict(fc_args)


            if (
                "transaction_ref" in args
                and name != "get_transaction_by_ref"
            ):

                ref=args.pop("transaction_ref")

                tx = await get_transaction_by_ref(ref)

                if not tx:
                    raise ValueError(
                        "Transaction not found"
                    )

                args["transaction_id"]=tx["_id"]



            for key in [
                "initiator_id",
                "counterparty_id",
                "buyer_id",
                "seller_id",
                "user_id"
            ]:

                if key in args and isinstance(args[key],str):

                    args[key]=ObjectId(args[key])



            result = await FUNCTION_MAP[name](**args)

            return self._serialize_bson(result)


        except Exception as e:

            print(
                "[CLAUDE TOOL ERROR]",
                e
            )

            return {
                "error":str(e)
            }



    def _serialize_bson(self,data):

        if isinstance(data,list):
            return [
                self._serialize_bson(x)
                for x in data
            ]

        if isinstance(data,dict):
            return {
                k:self._serialize_bson(v)
                for k,v in data.items()
            }

        if isinstance(data,ObjectId):
            return str(data)

        if hasattr(data,"isoformat"):
            return data.isoformat()

        return data