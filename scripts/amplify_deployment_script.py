import os
import sys
import boto3
import yaml
import argparse
from zipfile import ZipFile
import requests
from botocore.exceptions import ClientError
import getpass
import pwd
from git import Repo
from git.exc import InvalidGitRepositoryError, NoSuchPathError


def get_username():
    methods = [
        lambda: os.getenv('USER'),
        lambda: os.getenv('USERNAME'),
        getpass.getuser,
        lambda: pwd.getpwuid(os.getuid()).pw_name,
        lambda: 'default_user'
    ]

    for method in methods:
        try:
            username = method()
            if username and username != 'root':
                return username
        except:
            continue

    return 'default_user'


def load_config(config_file='config.yaml'):
    try:
        with open(config_file, 'r') as file:
            all_config = yaml.safe_load(file)

        username = get_username()
        print(f"Detected username: {username}")
        print(f"Available users in config: {list(all_config.keys())}")

        config = all_config.get(username, {})
        if not config:
            print(f"Warning: No configuration found for user '{username}'. Using default configuration.")
            config = next(iter(all_config.values()), {})

        print(f"Loaded config for user '{username}' from: {os.path.abspath(config_file)}")

        return config
    except FileNotFoundError:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        config_path = os.path.join(script_dir, config_file)
        try:
            with open(config_path, 'r') as file:
                all_config = yaml.safe_load(file)
            username = get_username()
            print(f"Detected username: {username}")
            print(f"Available users in config: {list(all_config.keys())}")
            config = all_config.get(username, {})
            if not config:
                print(f"Warning: No configuration found for user '{username}'. Using default configuration.")
                config = next(iter(all_config.values()), {})
            print(f"Loaded config for user '{username}' from: {config_path}")
            return config
        except FileNotFoundError:
            print(f"Config file not found in current directory or script directory.")
            print(f"Looked in: {os.getcwd()} and {script_dir}")
            return {}
    except yaml.YAMLError as e:
        print(f"Error parsing config file: {e}")
        sys.exit(1)


def set_aws_profile(config):
    aws_profile = config.get('AWS_PROFILE')
    if aws_profile:
        os.environ['AWS_PROFILE'] = aws_profile
        print(f"Set AWS_PROFILE to: {aws_profile}")
    else:
        print("Warning: AWS_PROFILE not found in config. Using default AWS credentials.")


def parse_arguments():
    parser = argparse.ArgumentParser(description="AWS Amplify Deployment Script")
    parser.add_argument('action', choices=['deploy', 'list-branches', 'list-apps'], help="Action to perform")
    parser.add_argument('--app', help="Friendly name of the app")
    parser.add_argument('--branch', help="Branch name for deployment")
    parser.add_argument('--config', default='config.yaml', help="Path to config file")
    return parser.parse_args()


def validate_html_directory(directory):
    if not os.path.isdir(directory):
        raise ValueError(f"'{directory}' is not a valid directory")
    html_files = [f for f in os.listdir(directory) if f.endswith('.html')]
    if not html_files:
        raise ValueError(f"No HTML files found in '{directory}'")


def zip_directory(folder_path, zip_path):
    with ZipFile(zip_path, 'w') as zipf:
        for root, _, files in os.walk(folder_path):
            for file in files:
                file_path = os.path.join(root, file)
                zipf.write(file_path, arcname=os.path.relpath(file_path, folder_path))


def upload_file_to_url(file_path, url):
    try:
        with open(file_path, 'rb') as f:
            response = requests.put(url, data=f)
        response.raise_for_status()
        return response
    except requests.RequestException as e:
        print(f"Error uploading file: {e}")
        sys.exit(1)


def create_deployment(app_id, branch_id):
    client = boto3.client('amplify')
    try:
        response = client.create_deployment(appId=app_id, branchName=branch_id)
        return response
    except ClientError as e:
        print(f"Error creating deployment: {e}")
        sys.exit(1)


def start_deployment(app_id, branch_id, job_id):
    client = boto3.client('amplify')
    try:
        response = client.start_deployment(appId=app_id, branchName=branch_id, jobId=job_id)
        return response
    except ClientError as e:
        print(f"Error starting deployment: {e}")
        sys.exit(1)


def list_branches(app_id):
    client = boto3.client('amplify')
    try:
        response = client.list_branches(appId=app_id)
        return response['branches']
    except ClientError as e:
        print(f"Error listing branches: {e}")
        sys.exit(1)


def get_app_info(config, app_name=None):
    current_dir = os.getcwd()

    if not app_name:
        # Try to find an app whose repo_root is a parent of the current directory
        for name, info in config.get('apps', {}).items():
            repo_root = info.get('repo_root', '')
            if repo_root and current_dir.startswith(repo_root):
                app_name = name
                break

    if not app_name:
        print("Error: No app specified and couldn't determine app from current directory.")
        sys.exit(1)

    app_info = config.get('apps', {}).get(app_name)
    if not app_info:
        print(f"Error: App '{app_name}' not found in config.")
        sys.exit(1)

    return app_name, app_info


def deploy(app_id, branch_id, directory):
    try:
        validate_html_directory(directory)
        zip_file_name = 'artifacts.zip'
        zip_directory(directory, zip_file_name)

        print(f"Creating deployment for app {app_id}, branch {branch_id}")
        deployment = create_deployment(app_id, branch_id)
        upload_url = deployment['zipUploadUrl']
        job_id = deployment['jobId']
        print(f'Created job: {job_id}')

        print(f"Uploading artifacts to URL: {upload_url}")
        upload_file_to_url(zip_file_name, upload_url)

        print(f"Starting deployment for job: {job_id}")
        start_deployment(app_id, branch_id, job_id)
        print(f'Started deployment: {job_id}')

        os.remove(zip_file_name)
        print(f"Deployment process completed for job: {job_id}")
    except Exception as e:
        print(f"Error during deployment: {str(e)}")
        sys.exit(1)


def get_current_git_branch():
    try:
        repo = Repo(os.getcwd(), search_parent_directories=True)
        return repo.active_branch.name
    except (InvalidGitRepositoryError, NoSuchPathError):
        print("Warning: Not in a Git repository.")
        return None


def branch_exists(app_id, branch_name):
    client = boto3.client('amplify')
    try:
        response = client.get_branch(appId=app_id, branchName=branch_name)
        return True
    except client.exceptions.NotFoundException:
        return False


def create_branch(app_id, branch_name):
    client = boto3.client('amplify')
    try:
        response = client.create_branch(appId=app_id, branchName=branch_name)
        print(f"Created new branch '{branch_name}' in Amplify app.")
        return True
    except ClientError as e:
        print(f"Error creating branch: {e}")
        return False


def get_deployment_branch(app_id, config_branch, git_branch):
    if config_branch:
        if branch_exists(app_id, config_branch):
            return config_branch
        else:
            print(f"Warning: Configured branch '{config_branch}' does not exist in Amplify app.")

    if git_branch:
        if branch_exists(app_id, git_branch):
            return git_branch
        else:
            print(f"Branch '{git_branch}' does not exist in Amplify app.")
            create = input(f"Do you want to create '{git_branch}' branch in Amplify? (y/n): ").lower().strip()
            if create == 'y':
                if create_branch(app_id, git_branch):
                    return git_branch

    print("No valid branch found. Available branches:")
    branches = list_branches(app_id)
    for branch in branches:
        print(f"- {branch['branchName']}")

    while True:
        branch_name = input("Enter the name of the branch you want to deploy to: ").strip()
        if branch_exists(app_id, branch_name):
            return branch_name
        else:
            create = input(f"Branch '{branch_name}' does not exist. Do you want to create it? (y/n): ").lower().strip()
            if create == 'y':
                if create_branch(app_id, branch_name):
                    return branch_name


def main():
    args = parse_arguments()
    config = load_config(args.config)

    print(f"Current working directory: {os.getcwd()}")
    print(f"Script location: {os.path.dirname(os.path.abspath(__file__))}")

    set_aws_profile(config)

    if args.action == 'list-apps':
        print("Available apps:")
        for app_name, app_info in config.get('apps', {}).items():
            print(f"- {app_name}")
            print(f"  Repo root: {app_info.get('repo_root', 'Not specified')}")
            print(f"  Build directory: {app_info.get('build_directory', 'Not specified')}")
        return

    app_name, app_info = get_app_info(config, args.app)
    app_id = app_info['app_id']
    repo_root = app_info.get('repo_root')
    build_directory = app_info.get('build_directory')

    print(f"Selected app: {app_name} (ID: {app_id})")
    print(f"Repo root: {repo_root}")
    print(f"Build directory: {build_directory}")

    if args.action == 'list-branches':
        branches = list_branches(app_id)
        print(f"Available branches for {app_name}:")
        for branch in branches:
            print(f"- {branch['branchName']}")
    elif args.action == 'deploy':
        config_branch = args.branch or app_info.get('default_branch')
        git_branch = get_current_git_branch()

        branch_id = get_deployment_branch(app_id, config_branch, git_branch)

        if not branch_id or not build_directory:
            print("Error: Branch ID and build directory are required for deployment.")
            sys.exit(1)

        print(f"Deploying {app_name} (ID: {app_id}) branch '{branch_id}' from {build_directory}")
        deploy(app_id, branch_id, build_directory)
        print("Deployment action completed.")


if __name__ == "__main__":
    main()