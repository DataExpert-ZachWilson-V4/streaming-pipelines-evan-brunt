import ast
import sys
import requests
import json
from pyspark.sql import SparkSession
from pyspark.sql.functions import col, from_json, udf, to_date, hash, session_window
from pyspark.sql.types import (
    StringType,
    IntegerType,
    TimestampType,
    StructType,
    StructField,
    MapType,
)
from awsglue.utils import getResolvedOptions
from awsglue.context import GlueContext
from awsglue.job import Job

# TODO PUT YOUR API KEY HERE
GEOCODING_API_KEY = "23EAA7D652D0178606418C42F4DFDEC3"


def geocode_ip_address(ip_address):
    url = "https://api.ip2location.io"
    response = requests.get(url, params={"ip": ip_address, "key": GEOCODING_API_KEY})

    if response.status_code != 200:
        # Return empty dict if request failed
        return {}

    data = json.loads(response.text)

    # Extract the country and state from the response
    # This might change depending on the actual response structure
    country = data.get("country_code", "")
    state = data.get("region_name", "")
    city = data.get("city_name", "")

    return {"country": country, "state": state, "city": city}


spark = SparkSession.builder.getOrCreate()
args = getResolvedOptions(
    sys.argv,
    ["JOB_NAME", "ds", "output_table", "kafka_credentials", "checkpoint_location"],
)
run_date = args["ds"]
output_table = args["output_table"]
checkpoint_location = args["checkpoint_location"]
kafka_credentials = ast.literal_eval(args["kafka_credentials"])
glueContext = GlueContext(spark.sparkContext)
spark = glueContext.spark_session

# Retrieve Kafka credentials from environment variables
kafka_key = kafka_credentials["KAFKA_WEB_TRAFFIC_KEY"]
kafka_secret = kafka_credentials["KAFKA_WEB_TRAFFIC_SECRET"]
kafka_bootstrap_servers = kafka_credentials["KAFKA_WEB_BOOTSTRAP_SERVER"]
kafka_topic = kafka_credentials["KAFKA_TOPIC"]

if kafka_key is None or kafka_secret is None:
    raise ValueError(
        "KAFKA_WEB_TRAFFIC_KEY and KAFKA_WEB_TRAFFIC_SECRET must be set as environment variables."
    )

# Kafka configuration

start_timestamp = f"{run_date}T00:00:00.000Z"

# Define the schema of the Kafka message value
schema = StructType(
    [
        StructField("url", StringType(), True),
        StructField("referrer", StringType(), True),
        StructField(
            "user_agent",
            StructType(
                [
                    StructField("family", StringType(), True),
                    StructField("major", StringType(), True),
                    StructField("minor", StringType(), True),
                    StructField("patch", StringType(), True),
                    StructField(
                        "device",
                        StructType(
                            [
                                StructField("family", StringType(), True),
                                StructField("major", StringType(), True),
                                StructField("minor", StringType(), True),
                                StructField("patch", StringType(), True),
                            ]
                        ),
                        True,
                    ),
                    StructField(
                        "os",
                        StructType(
                            [
                                StructField("family", StringType(), True),
                                StructField("major", StringType(), True),
                                StructField("minor", StringType(), True),
                                StructField("patch", StringType(), True),
                            ]
                        ),
                        True,
                    ),
                ]
            ),
            True,
        ),
        StructField(
            "headers", MapType(keyType=StringType(), valueType=StringType()), True
        ),
        StructField("host", StringType(), True),
        StructField("ip", StringType(), True),
        StructField("event_time", TimestampType(), True),
        StructField("user_id", StringType(), True),
    ]
)


spark.sql(f"""
CREATE TABLE IF NOT EXISTS {output_table} (
  session_id VARCHAR(100), -- unique id of the session
  user_id VARCHAR(100), -- user_id if the user was logged in, NULL otherwise
  session_start TIMESTAMP, -- when the session started
  session_end TIMESTAMP, -- when the session expired (after 5 minutes of inactivity)
  event_count BIGINT, -- number of events that occurred during the session
  session_date DATE, -- date of the session. this is used for partitioning
  city VARCHAR(100), -- city user is in
  state VARCHAR(100), -- state user is in
  country VARCHAR(100), -- country user is in
  operating_system VARCHAR(100), -- what operating system the user is using
  browser_family VARCHAR(100), -- what browser the user is using 
  logged_in BOOLEAN -- true if user is logged in, false otherwise
)
USING ICEBERG
PARTITIONED BY (session_date)
""")

# Read from Kafka in batch mode
kafka_df = (
    spark.readStream.format("kafka")
    .option("kafka.bootstrap.servers", kafka_bootstrap_servers)
    .option("subscribe", kafka_topic)
    .option("startingOffsets", "latest")
    .option("maxOffsetsPerTrigger", 10000)
    .option("kafka.security.protocol", "SASL_SSL")
    .option("kafka.sasl.mechanism", "PLAIN")
    .option(
        "kafka.sasl.jaas.config",
        f'org.apache.kafka.common.security.plain.PlainLoginModule required username="{kafka_key}" password="{kafka_secret}";',
    )
    .load()
)


def decode_col(column):
    return column.decode("utf-8")


decode_udf = udf(decode_col, StringType())

geocode_schema = StructType(
    [
        StructField("country", StringType(), True),
        StructField("city", StringType(), True),
        StructField("state", StringType(), True),
    ]
)

geocode_udf = udf(geocode_ip_address, geocode_schema)

tumbling_window_df = (
    kafka_df.withColumn("decoded_value", decode_udf(col("value")))
    .withColumn("value", from_json(col("decoded_value"), schema))
    .withColumn("geodata", geocode_udf(col("value.ip")))
    .withWatermark("timestamp", "5 minutes")
)

by_session = (
    tumbling_window_df.groupBy(
        session_window(
            col("event_time"), "5 minutes"
        ),  # using 5 minute window based on prompt
        col("value.ip").alias("ip"),
        col("value.user_id").alias("user_id"),
        col("value.user_agent.family").alias(
            "browser_family"
        ),  # user agent -> family is browser name
        col("value.user_agent.os.family").alias(
            "operating_system"
        ),  # useragent -> os -> family is operating system
        col("geodata.country").alias("country"),
        col("geodata.city").alias("city"),
        col("geodata.state").alias("state"),
    )
    .count()
    .select(
        hash(
            col("ip"),
            col("user_id"),
            col("window.start"),
        )  # hashing these for the session_id because they will identify a unique user including when someone goes from logged out to logged in
        .cast("string")
        .alias("session_id"),
        col("user_id"),
        col("window.start").alias(
            "session_start"
        ),  # start of the window is the start of the session
        col("window.end").alias("session_end"),
        col("count").alias("event_count"),
        col("window.start").cast("date").alias("session_date"),
        col("city"),
        col("state"),
        col("country"),
        col("operating_system"),
        col("browser_family"),
        col("user_id")
        .isNotNull()
        .alias("logged_in"),  # if user_id is null the user is not logged in
    )
)

query = (
    by_session.writeStream.format("iceberg")
    .outputMode("append")
    .trigger(processingTime="5 seconds")
    .option("fanout-enabled", "true")
    .option("checkpointLocation", checkpoint_location)
    .toTable(output_table)
)

job = Job(glueContext)
job.init(args["JOB_NAME"], args)

# PLEASE DO NOT REMOVE TIMEOUT
query.awaitTermination(timeout=60 * 60)
