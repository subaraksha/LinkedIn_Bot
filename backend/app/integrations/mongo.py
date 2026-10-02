from pymongo import AsyncMongoClient
import certifi


async def inspect_mongo(uri: str, database: str) -> dict:
    client = AsyncMongoClient(
        uri, serverSelectionTimeoutMS=8000, tlsCAFile=certifi.where()
    )
    try:
        await client.admin.command("ping")
        hello = await client.admin.command("hello")
        names = await client[database].list_collection_names()
        return {
            "reachable": True,
            "replica_set": bool(hello.get("setName")),
            "collection_count": len(names),
        }
    finally:
        await client.close()
