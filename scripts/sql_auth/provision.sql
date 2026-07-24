SET NOCOUNT ON;
SET XACT_ABORT ON;

IF DB_ID(N'fastmssql_validation') IS NULL
    CREATE DATABASE [fastmssql_validation];
IF DB_ID(N'fastmssql_upstream_regression') IS NULL
    CREATE DATABASE [fastmssql_upstream_regression];
GO

IF SUSER_ID(N'fastmssql_owner') IS NULL
    CREATE LOGIN [fastmssql_owner]
      WITH PASSWORD = N'$(OwnerPassword)', CHECK_POLICY = OFF;
ELSE
    ALTER LOGIN [fastmssql_owner]
      WITH PASSWORD = N'$(OwnerPassword)', CHECK_POLICY = OFF;

IF SUSER_ID(N'fastmssql_readonly') IS NULL
    CREATE LOGIN [fastmssql_readonly]
      WITH PASSWORD = N'$(ReadonlyPassword)', CHECK_POLICY = OFF;
ELSE
    ALTER LOGIN [fastmssql_readonly]
      WITH PASSWORD = N'$(ReadonlyPassword)', CHECK_POLICY = OFF;

IF SUSER_ID(N'fastmssql_denied') IS NULL
    CREATE LOGIN [fastmssql_denied]
      WITH PASSWORD = N'$(DeniedPassword)', CHECK_POLICY = OFF;
ELSE
    ALTER LOGIN [fastmssql_denied]
      WITH PASSWORD = N'$(DeniedPassword)', CHECK_POLICY = OFF;

USE [fastmssql_validation];

IF USER_ID(N'fastmssql_owner') IS NULL
    CREATE USER [fastmssql_owner] FOR LOGIN [fastmssql_owner];
IF USER_ID(N'fastmssql_readonly') IS NULL
    CREATE USER [fastmssql_readonly] FOR LOGIN [fastmssql_readonly];
IF USER_ID(N'fastmssql_denied') IS NULL
    CREATE USER [fastmssql_denied] FOR LOGIN [fastmssql_denied];

IF IS_ROLEMEMBER(N'db_owner', N'fastmssql_owner') <> 1
    ALTER ROLE [db_owner] ADD MEMBER [fastmssql_owner];
IF IS_ROLEMEMBER(N'db_datareader', N'fastmssql_readonly') <> 1
    ALTER ROLE [db_datareader] ADD MEMBER [fastmssql_readonly];
DENY INSERT, UPDATE, DELETE, CREATE TABLE TO [fastmssql_readonly];
DENY SELECT, INSERT, UPDATE, DELETE, CREATE TABLE TO [fastmssql_denied];

USE [fastmssql_upstream_regression];

IF USER_ID(N'fastmssql_owner') IS NULL
    CREATE USER [fastmssql_owner] FOR LOGIN [fastmssql_owner];
IF IS_ROLEMEMBER(N'db_owner', N'fastmssql_owner') <> 1
    ALTER ROLE [db_owner] ADD MEMBER [fastmssql_owner];

SELECT
  SERVERPROPERTY('Edition') AS edition,
  SERVERPROPERTY('ProductVersion') AS product_version,
  DB_ID(N'fastmssql_validation') AS validation_database_id,
  DB_ID(N'fastmssql_upstream_regression') AS upstream_database_id;
