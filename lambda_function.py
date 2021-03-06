#!/usr/bin/env python
import glob
import json
import os
import uuid
import boto3
import datetime
import random
from urllib.parse import urlparse
import logging
from botocore.client import ClientError
from botocore.retries import base

logger = logging.getLogger()
logger.setLevel(logging.INFO)

def lambda_handler(event, context):
    
    logging.info('event::{}'.format(event))

    assetID = str(uuid.uuid4())
    sourceS3Bucket = event['Records'][0]['s3']['bucket']['name']
    sourceS3Key = event['Records'][0]['s3']['object']['key']
    sourceS3 = 's3://'+ sourceS3Bucket + '/' + sourceS3Key
    destinationS3 = 's3://' + os.environ['DestinationBucket']
    outputS3 = 's3://' + os.environ['DestinationBucket'] + '/' + sourceS3Key
    mediaConvertRole = os.environ['MediaConvertRole']
    region = os.environ['AWS_DEFAULT_REGION']
    baseurl = os.environ['BaseURL']
    statusCode = 200
    jobs = []
    job = {}
    
    # Use MediaConvert SDK UserMetadata to tag jobs with the assetID 
    # Events from MediaConvert will have the assetID in UserMedata
    jobMetadata = {}
    jobMetadata['assetID'] = assetID
    jobMetadata['input'] = sourceS3
    jobMetadata['output'] = outputS3
    jobMetadata['BaseURL'] = baseurl
    jobMetadata['Filename'] = os.path.basename(sourceS3Key)
    
    try:    
        # Build a list of jobs to run against the input.  Use the settings files in WatchFolder/jobs
        # if any exist.  Otherwise, use the default job.

        jobInput = {}
        # Use Default job settings in the lambda zip file in the current working directory
        with open('job-template.json') as json_data:
            jobInput['filename'] = 'Default'
            logger.info('jobInput: %s', jobInput['filename'])

            jobInput['settings'] = json.load(json_data)
            logger.info(json.dumps(jobInput['settings']))

            jobs.append(jobInput)
                 
        # get the account-specific mediaconvert endpoint for this region
        mediaconvert_client = boto3.client('mediaconvert', region_name=region)
        endpoints = mediaconvert_client.describe_endpoints()

        # add the account-specific endpoint to the client session 
        client = boto3.client('mediaconvert', region_name=region, endpoint_url=endpoints['Endpoints'][0]['Url'], verify=False)
        
        for j in jobs:
            jobSettings = j['settings']
            jobFilename = j['filename']

            # Save the name of the settings file in the job userMetadata
            jobMetadata['settings'] = jobFilename

            # Update the job settings with the source video from the S3 event 
            jobSettings['Inputs'][0]['FileInput'] = sourceS3

            # Update the job settings with the destination paths for converted videos.  We want to replace the
            # destination bucket of the output paths in the job settings, but keep the rest of the
            # path
            destinationS3 = 's3://' + os.environ['DestinationBucket'] + '/' + os.path.dirname(sourceS3Key)
            # destinationURL = 'https://' + os.environ['DestinationBucket'] + '/' + os.path.dirname(sourceS3Key)
            destinationURL = baseurl + '/' + os.path.dirname(sourceS3Key)
            
            for outputGroup in jobSettings['OutputGroups']:
                
                logger.info("outputGroup['OutputGroupSettings']['Type'] == %s", outputGroup['OutputGroupSettings']['Type']) 
  
                if outputGroup['OutputGroupSettings']['Type'] == 'HLS_GROUP_SETTINGS':
                    templateDestination = outputGroup['OutputGroupSettings']['HlsGroupSettings']['Destination']
                    templateDestinationKey = urlparse(templateDestination).path
                    logger.info("templateDestinationKey == %s", templateDestinationKey)
                    outputGroup['OutputGroupSettings']['HlsGroupSettings']['Destination'] = destinationS3+templateDestinationKey
                    outputGroup['OutputGroupSettings']['HlsGroupSettings']['BaseUrl'] = destinationURL+templateDestinationKey
                    
                    jobMetadata['Manifest'] = destinationURL+templateDestinationKey+os.path.splitext(jobMetadata['Filename'])[0]+'.m3u8'
                
                else:
                    logger.error("Exception: Unknown Output Group Type %s", outputGroup['OutputGroupSettings']['Type'])
                    statusCode = 500
            
            logger.info(json.dumps(jobSettings))

            # Convert the video using AWS Elemental MediaConvert
            job = client.create_job(Role=mediaConvertRole, UserMetadata=jobMetadata, Tags=jobMetadata, Settings=jobSettings)

    except Exception as e:
        logger.error('Exception: %s', e)
        statusCode = 500
        raise

    finally:
        return {
            'statusCode': statusCode,
            'body': json.dumps(job, indent=4, sort_keys=True, default=str),
            'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'}
        }